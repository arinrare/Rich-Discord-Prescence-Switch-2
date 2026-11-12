"""Minimal obs-websocket v5 client (WebSocket + JSON, reconnecting)."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import uuid
from typing import Any

import numpy as np
import websocket

from .vision import decode_data_uri


class OBSUnavailable(RuntimeError):
    pass


class OBSAuthError(OBSUnavailable):
    pass


class OBSRequestError(OBSUnavailable):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def _auth_response(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode("utf-8")).digest()).decode("ascii")


class OBSClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4455,
        password: str = "",
        source_name: str = "Elgato Neo",
        timeout: float = 5.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.password = password
        self.source_name = source_name
        self.timeout = timeout
        self.log = logger or logging.getLogger("switch2_presence.obs")
        self._ws: websocket.WebSocket | None = None
        self._lock = threading.RLock()
        self._next_retry = 0.0
        self._retry_delay = 2.0
        self.last_error: str | None = None
        self.version: str | None = None

    # ---- connection ------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._ws is not None

    def connect(self) -> None:
        with self._lock:
            self._close_socket()
            url = f"ws://{self.host}:{self.port}"
            try:
                ws = websocket.create_connection(url, timeout=self.timeout)
            except Exception as exc:
                self.last_error = f"cannot connect to {url}: {exc}"
                raise OBSUnavailable(self.last_error) from exc
            try:
                ws.settimeout(self.timeout)
                hello_raw = ws.recv()
                hello = json.loads(hello_raw)
                if hello.get("op") != 0:
                    raise OBSUnavailable("unexpected hello from OBS")
                data = hello.get("d") or {}
                obs_version = data.get("obsWebSocketVersion")
                auth = data.get("authentication") or {}
                identify: dict[str, Any] = {"rpcVersion": 1, "eventSubscriptions": 0}
                if auth:
                    if not self.password:
                        raise OBSAuthError("OBS requires a password but none is configured")
                    identify["authentication"] = _auth_response(
                        self.password, str(auth.get("salt", "")), str(auth.get("challenge", ""))
                    )
                ws.send(json.dumps({"op": 1, "d": identify}))
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    message = json.loads(ws.recv())
                    op = message.get("op")
                    if op == 2:
                        self._ws = ws
                        self.version = obs_version
                        self._retry_delay = 2.0
                        self.last_error = None
                        self.log.info("OBS connected (obs-websocket %s)", obs_version)
                        return
                    if op == 9:
                        raise OBSAuthError("OBS authentication failed")
                raise OBSUnavailable("OBS did not acknowledge identification")
            except OBSUnavailable:
                ws.close()
                raise
            except Exception as exc:
                ws.close()
                self.last_error = f"OBS handshake failed: {exc}"
                raise OBSUnavailable(self.last_error) from exc

    def _close_socket(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def close(self) -> None:
        with self._lock:
            self._close_socket()

    def _ensure(self) -> None:
        if self._ws is not None:
            return
        now = time.monotonic()
        if now < self._next_retry:
            raise OBSUnavailable(self.last_error or "waiting to reconnect")
        try:
            self.connect()
        except OBSUnavailable:
            self._next_retry = time.monotonic() + self._retry_delay
            self._retry_delay = min(30.0, self._retry_delay * 1.8)
            raise

    def _fail(self, exc: BaseException) -> OBSUnavailable:
        self.last_error = str(exc) or exc.__class__.__name__
        self._close_socket()
        return OBSUnavailable(self.last_error)

    def request(self, request_type: str, request_data: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            assert self._ws is not None
            request_id = uuid.uuid4().hex
            payload = {
                "op": 6,
                "d": {
                    "requestType": request_type,
                    "requestId": request_id,
                    "requestData": request_data or {},
                },
            }
            try:
                self._ws.settimeout(self.timeout)
                self._ws.send(json.dumps(payload))
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    message = json.loads(self._ws.recv())
                    op = message.get("op")
                    if op == 9:
                        raise self._fail(OBSAuthError("OBS closed the session during authentication"))
                    if op != 7:
                        continue
                    body = message.get("d") or {}
                    if body.get("requestId") != request_id:
                        continue
                    status = body.get("requestStatus") or {}
                    if not status.get("result", True):
                        code = status.get("code")
                        comment = status.get("comment") or request_type
                        raise OBSRequestError(f"{request_type} failed: {comment}", code)
                    return body.get("responseData") or {}
                raise OBSUnavailable(f"{request_type} timed out")
            except OBSRequestError:
                raise
            except OBSUnavailable:
                raise
            except Exception as exc:
                raise self._fail(exc) from exc

    # ---- API -------------------------------------------------------------
    def screenshot(self, width: int = 320, height: int = 180, image_format: str = "png") -> np.ndarray:
        data = self.request(
            "GetSourceScreenshot",
            {
                "sourceName": self.source_name,
                "imageFormat": image_format,
                "imageWidth": int(width),
                "imageHeight": int(height),
            },
        )
        image_data = data.get("imageData")
        if not image_data:
            raise OBSRequestError("GetSourceScreenshot returned no image")
        return decode_data_uri(str(image_data))

    def scene_name(self) -> str | None:
        try:
            data = self.request("GetCurrentProgramScene")
            return data.get("currentProgramSceneName") or data.get("sceneName")
        except OBSUnavailable:
            return None

    def list_capture_sources(self) -> list[str]:
        try:
            data = self.request("GetInputList", {"inputKind": "dshow_input"})
            return [str(item.get("inputName")) for item in data.get("inputs", []) if item.get("inputName")]
        except OBSUnavailable:
            return []

    def probe(self, width: int = 320, height: int = 180) -> tuple[np.ndarray, float, float, float]:
        from .vision import power_metrics

        image = self.screenshot(width, height)
        metrics = power_metrics(image)
        return image, metrics.mean, metrics.std, metrics.edges
