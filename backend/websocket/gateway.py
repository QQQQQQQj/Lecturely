"""RealtimeGateway：/ws/live 连接管理、事件广播（seq 分配）、客户端消息处理。

- 单连接可订阅活动会话；多标签页可同时连接（广播扇出）。
- seq 由网关在广播时统一递增分配（前端据此去重与断线补拉）。
- 客户端消息做状态机校验；未知类型丢弃并记日志。
"""
from __future__ import annotations

import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from ..config import AppConfig
from ..core.session_manager import LiveSessionConfig, SessionManager
from ..errors import LecturelyError
from ..logging_setup import get_logger
from . import events as ev

logger = get_logger(__name__)


class RealtimeGateway:
    def __init__(self, app_cfg: AppConfig):
        self._app_cfg = app_cfg
        self._connections: set[WebSocket] = set()
        self._seq = 0
        self._seq_lock = asyncio.Lock()
        self._stop_task: asyncio.Task | None = None
        self.session_manager = SessionManager(app_cfg, self.broadcast)

    async def broadcast(self, event: dict) -> None:
        async with self._seq_lock:
            self._seq += 1
            event["seq"] = self._seq
        if not self._connections:
            return
        dead = []
        for ws in list(self._connections):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)

    async def handle_connection(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.info("WS 客户端连接（当前 %d 个）", len(self._connections))
        try:
            # 连接即下发当前会话状态（便于迟到进入者同步）
            status = self.session_manager.current_status()
            await ws.send_json(ev.make_event("connection.ready", payload={
                "session": status, "tail_seq": self._seq,
            }))
            while True:
                msg = await ws.receive_json()
                await self._handle_client_message(ws, msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("WS 连接异常：%s", e)
        finally:
            self._connections.discard(ws)
            logger.info("WS 客户端断开（剩余 %d 个）", len(self._connections))

    async def _handle_client_message(self, ws: WebSocket, msg: dict) -> None:
        mtype = msg.get("type")
        payload = msg.get("payload") or {}
        try:
            if mtype == ev.C_SESSION_START:
                cfg = LiveSessionConfig(
                    input_mode=payload.get("input_mode", "mic"),
                    mic_device=payload.get("mic_device", "default"),
                    system_device=payload.get("system_device", "default"),
                    source_language=payload.get("source_language", "auto"),
                    target_language=payload.get("target_language", "zh-CN"),
                    save_recording=bool(payload.get("save_recording", True)),
                    folder_id=payload.get("folder_id"),
                    title=payload.get("title"),
                )
                await self.session_manager.start(cfg)
            elif mtype == ev.C_SESSION_PAUSE:
                await self.session_manager.pause()
            elif mtype == ev.C_SESSION_RESUME:
                await self.session_manager.resume()
            elif mtype == ev.C_SESSION_STOP:
                # 后台执行收尾（flush/翻译排空可能需要数秒），不阻塞接收循环；
                # 前端依靠 session.processing / session.completed 事件更新状态。
                if self._stop_task is None or self._stop_task.done():
                    self._stop_task = asyncio.create_task(self._safe_stop())
            elif mtype == ev.C_SESSION_SUBSCRIBE:
                # 断线恢复：当前实现下发活动会话状态（事件缓冲在后续里程碑增强）
                await ws.send_json(ev.make_event("connection.ready", payload={
                    "session": self.session_manager.current_status(), "tail_seq": self._seq,
                }))
            elif mtype == ev.C_TRANSLATION_RETRY:
                segment_id = payload.get("segment_id")
                if segment_id:
                    await self.session_manager.retry_translation(segment_id)
            else:
                logger.debug("忽略未知 WS 消息类型：%s", mtype)
        except LecturelyError as e:
            await ws.send_json(ev.error_event(
                scope=e.info.scope, code=e.info.code, message=e.info.message,
                suggestion=e.info.suggestion, retryable=e.info.retryable))
        except Exception as e:  # noqa: BLE001
            logger.warning("处理客户端消息失败（%s）：%s", mtype, e)
            await ws.send_json(ev.error_event(
                scope="session", code="internal_error",
                message=f"操作失败：{e}", retryable=True))

    async def _safe_stop(self) -> None:
        try:
            await self.session_manager.stop()
        except LecturelyError as e:
            await self.broadcast(ev.error_event(
                scope=e.info.scope, code=e.info.code, message=e.info.message,
                suggestion=e.info.suggestion, retryable=e.info.retryable))
        except Exception as e:  # noqa: BLE001
            logger.warning("后台停止会话失败：%s", e)
            await self.broadcast(ev.error_event(
                scope="session", code="stop_failed",
                message=f"停止会话失败：{e}", retryable=True))

    async def shutdown(self) -> None:
        await self.session_manager.shutdown()
        for ws in list(self._connections):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()


_gateway: RealtimeGateway | None = None


def get_gateway(app_cfg: AppConfig) -> RealtimeGateway:
    global _gateway
    if _gateway is None:
        _gateway = RealtimeGateway(app_cfg)
    return _gateway
