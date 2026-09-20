from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from ..models import InboundMessage
from ..paths import INBOX_DIR, QR_FILE, WEIXIN_META_FILE, WEIXIN_SEEN_FILE, WEIXIN_SYNC_FILE
from ..secrets_store import SecretStore

log = logging.getLogger(__name__)

OFFICIAL_PLUGIN_VERSION = "2.4.9"
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"
APP_ID = "bot"
BOT_AGENT = "OpenClaw"
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def version_number(version: str) -> int:
    major, minor, patch = (int(x) for x in version.split(".")[:3])
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


APP_CLIENT_VERSION = str(version_number(OFFICIAL_PLUGIN_VERSION))


def random_wechat_uin() -> str:
    return base64.b64encode(str(secrets.randbits(32)).encode("utf-8")).decode("ascii")


def common_headers() -> dict[str, str]:
    return {"iLink-App-Id": APP_ID, "iLink-App-ClientVersion": APP_CLIENT_VERSION}


def post_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": random_wechat_uin(),
        **common_headers(),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def base_info() -> dict[str, str]:
    return {"channel_version": OFFICIAL_PLUGIN_VERSION, "bot_agent": BOT_AGENT}


def safe_filename(name: str) -> str:
    name = Path(name or "file.bin").name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or "file.bin"


def decode_media_aes_key(value: str) -> bytes:
    raw = base64.b64decode(value)
    if len(raw) == 16:
        return raw
    if len(raw) == 32:
        try:
            decoded = bytes.fromhex(raw.decode("ascii"))
            if len(decoded) == 16:
                return decoded
        except Exception:
            pass
    if len(value) == 32:
        decoded = bytes.fromhex(value)
        if len(decoded) == 16:
            return decoded
    raise ValueError("unsupported AES key encoding")


class WeixinChannel:
    def __init__(self, secrets_store: SecretStore):
        self.secrets = secrets_store
        self.http = requests.Session()
        self.stop_event = threading.Event()
        self.poll_thread: threading.Thread | None = None
        self.login_thread: threading.Thread | None = None
        self.on_message: Callable[[InboundMessage], None] | None = None
        self.state_lock = threading.RLock()
        self.state: dict[str, Any] = {
            "status": "disconnected",
            "detail": "",
            "protocol": OFFICIAL_PLUGIN_VERSION,
            "qr_updated_at": None,
        }
        self.verify_event = threading.Event()
        self.verify_code: str | None = None
        self.last_context_by_user: dict[str, str] = {}
        self.seen_order = self._load_json(WEIXIN_SEEN_FILE, [])
        if not isinstance(self.seen_order, list):
            self.seen_order = []
        self.seen_order = [str(x) for x in self.seen_order][-3000:]
        self.seen = set(self.seen_order)

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _set_state(self, status: str, detail: str = "", **extra: Any) -> None:
        with self.state_lock:
            self.state.update({"status": status, "detail": detail, **extra})

    def get_status(self) -> dict[str, Any]:
        with self.state_lock:
            return dict(self.state)

    def _meta(self) -> dict[str, Any] | None:
        data = self._load_json(WEIXIN_META_FILE, None)
        if not isinstance(data, dict):
            return None
        if not data.get("ilink_bot_id") or not data.get("base_url"):
            return None
        if not self.secrets.get("weixin_bot_token"):
            return None
        return data

    def _creds(self) -> dict[str, Any] | None:
        meta = self._meta()
        if not meta:
            return None
        return {**meta, "bot_token": self.secrets.get("weixin_bot_token")}

    def has_credentials(self) -> bool:
        return self._creds() is not None

    def _save_creds(self, data: dict[str, Any]) -> dict[str, Any]:
        token = str(data.get("bot_token") or "")
        if not token:
            raise RuntimeError("bot_token missing")
        self.secrets.set("weixin_bot_token", token)
        meta = {
            "ilink_bot_id": data.get("ilink_bot_id"),
            "ilink_user_id": data.get("ilink_user_id"),
            "base_url": data.get("base_url") or DEFAULT_BASE_URL,
            "saved_at": utc_now(),
            "protocol": OFFICIAL_PLUGIN_VERSION,
        }
        self._write_json(WEIXIN_META_FILE, meta)
        return {**meta, "bot_token": token}

    def _parse(self, resp: requests.Response, label: str) -> dict[str, Any]:
        text = resp.content.decode("utf-8", errors="replace")
        if not resp.ok:
            raise RuntimeError(f"{label} HTTP {resp.status_code}: {text[:500]}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label} returned non-JSON: {text[:500]}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{label} returned non-object JSON")
        return data

    def _post(self, base_url: str, endpoint: str, body: dict[str, Any], token: str | None, timeout: float, label: str) -> tuple[int, dict[str, Any]]:
        url = base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        resp = self.http.post(url, headers=post_headers(token), json=body, timeout=timeout)
        return resp.status_code, self._parse(resp, label)

    def _api_post(self, creds: dict[str, Any], endpoint: str, body: dict[str, Any], timeout: float, label: str) -> tuple[int, dict[str, Any]]:
        return self._post(
            str(creds["base_url"]), endpoint, {**body, "base_info": base_info()}, str(creds["bot_token"]), timeout, label
        )

    def start_login(self, force: bool = False) -> None:
        if self.login_thread and self.login_thread.is_alive():
            return
        # A forced re-login must first stop any old long-poll worker, otherwise
        # the stale credentials may keep consuming updates while the QR flow is
        # replacing them. Clearing the event afterwards also makes reconnect
        # work after the user explicitly pressed "Stop" in the control UI.
        if force and self.poll_thread and self.poll_thread.is_alive():
            self.stop_event.set()
            self.poll_thread.join(timeout=2.0)
        self.stop_event.clear()
        self.login_thread = threading.Thread(target=self._login_worker, args=(force,), daemon=True, name="weixin-login")
        self.login_thread.start()

    def submit_verify_code(self, code: str) -> None:
        self.verify_code = code.strip()
        self.verify_event.set()

    def _render_qr(self, content: str) -> None:
        import qrcode
        QR_FILE.parent.mkdir(parents=True, exist_ok=True)
        qrcode.make(content).save(QR_FILE)
        self._set_state("qr_ready", "请用普通微信扫一扫并确认授权", qr_updated_at=utc_now())

    def _fetch_qr(self, local_tokens: list[str]) -> str:
        _, data = self._post(
            DEFAULT_BASE_URL,
            f"/ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}",
            {"local_token_list": local_tokens[:10]},
            None,
            20,
            "get_bot_qrcode",
        )
        qrcode_value = str(data.get("qrcode") or "")
        content = str(data.get("qrcode_img_content") or "")
        if not qrcode_value or not content:
            raise RuntimeError("二维码接口缺少 qrcode/qrcode_img_content")
        self._render_qr(content)
        return qrcode_value

    def _qr_status(self, base_url: str, qrcode_value: str, verify_code: str | None = None) -> dict[str, Any]:
        endpoint = f"/ilink/bot/get_qrcode_status?qrcode={quote(qrcode_value, safe='')}"
        if verify_code:
            endpoint += f"&verify_code={quote(verify_code, safe='')}"
        try:
            resp = self.http.get(base_url.rstrip("/") + endpoint, headers=common_headers(), timeout=40)
            return self._parse(resp, "get_qrcode_status")
        except requests.Timeout:
            return {"status": "wait"}

    def _login_worker(self, force: bool) -> None:
        try:
            existing = self._creds()
            if existing and not force:
                self._set_state("connected", "已复用本地微信凭证")
                self.start_polling()
                return
            local_tokens = [str(existing["bot_token"])] if existing else []
            qrcode_value = self._fetch_qr(local_tokens)
            poll_base = DEFAULT_BASE_URL
            verify_code = None
            refresh_count = 0
            deadline = time.time() + 10 * 60
            while time.time() < deadline and not self.stop_event.is_set():
                data = self._qr_status(poll_base, qrcode_value, verify_code)
                status = str(data.get("status") or "")
                if status == "wait":
                    time.sleep(0.8)
                    continue
                if status == "scaned":
                    self._set_state("scanned", "已扫码，请在手机微信确认")
                    verify_code = None
                    continue
                if status == "need_verifycode":
                    self._set_state("need_verifycode", "手机微信要求验证码")
                    self.verify_event.clear()
                    if not self.verify_event.wait(timeout=180):
                        raise RuntimeError("验证码等待超时")
                    verify_code = self.verify_code
                    self.verify_code = None
                    continue
                if status == "scaned_but_redirect":
                    host = str(data.get("redirect_host") or "").strip()
                    if host:
                        poll_base = host if host.startswith("http") else f"https://{host}"
                    continue
                if status == "binded_redirect" and existing:
                    self._set_state("connected", "已绑定，复用本地凭证")
                    self.start_polling()
                    return
                if status in {"expired", "verify_code_blocked"}:
                    refresh_count += 1
                    if refresh_count > 5:
                        raise RuntimeError(f"二维码重复失效: {status}")
                    qrcode_value = self._fetch_qr(local_tokens)
                    poll_base = DEFAULT_BASE_URL
                    verify_code = None
                    continue
                if status == "confirmed":
                    creds = self._save_creds(
                        {
                            "bot_token": str(data.get("bot_token") or ""),
                            "ilink_bot_id": str(data.get("ilink_bot_id") or ""),
                            "ilink_user_id": data.get("ilink_user_id"),
                            "base_url": data.get("baseurl") or DEFAULT_BASE_URL,
                        }
                    )
                    if not creds.get("ilink_bot_id"):
                        raise RuntimeError("登录确认但 ilink_bot_id 缺失")
                    self._set_state("connected", "微信 Claw 已连接")
                    self.start_polling()
                    return
                time.sleep(0.8)
            raise RuntimeError("二维码登录超时")
        except Exception as exc:
            log.exception("Weixin login failed")
            self._set_state("error", str(exc))

    def start_polling(self) -> None:
        if self.poll_thread and self.poll_thread.is_alive():
            return
        if not self._creds():
            self._set_state("disconnected", "尚未登录微信")
            return
        self.stop_event.clear()
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="weixin-poll")
        self.poll_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._set_state("stopped", "已停止微信通道")

    def _load_cursor(self, bot_id: str) -> str:
        data = self._load_json(WEIXIN_SYNC_FILE, {})
        if isinstance(data, dict) and data.get("ilink_bot_id") == bot_id:
            return str(data.get("get_updates_buf") or "")
        return ""

    def _save_cursor(self, bot_id: str, cursor: str) -> None:
        self._write_json(WEIXIN_SYNC_FILE, {"ilink_bot_id": bot_id, "get_updates_buf": cursor, "updated_at": utc_now()})

    def _mark_seen(self, message_id: str) -> None:
        if not message_id or message_id in self.seen:
            return
        self.seen.add(message_id)
        self.seen_order.append(message_id)
        if len(self.seen_order) > 3000:
            old = self.seen_order.pop(0)
            self.seen.discard(old)
        self._write_json(WEIXIN_SEEN_FILE, self.seen_order)

    def _notify(self, creds: dict[str, Any], start: bool) -> None:
        endpoint = "/ilink/bot/msg/notifystart" if start else "/ilink/bot/msg/notifystop"
        try:
            self._api_post(creds, endpoint, {}, 10, "notify")
        except Exception:
            pass

    def _poll_loop(self) -> None:
        creds = self._creds()
        if not creds:
            return
        bot_id = str(creds["ilink_bot_id"])
        cursor = self._load_cursor(bot_id)
        self._notify(creds, True)
        self._set_state("connected", "微信消息监听中")
        try:
            while not self.stop_event.is_set():
                try:
                    _, data = self._api_post(creds, "/ilink/bot/getupdates", {"get_updates_buf": cursor}, 45, "getUpdates")
                    ret = data.get("ret")
                    errcode = data.get("errcode")
                    if ret not in (None, 0) or errcode not in (None, 0):
                        if ret == -14 or errcode == -14:
                            self._set_state("error", "微信凭证已失效，请重新扫码")
                            return
                        raise RuntimeError(f"getUpdates ret={ret} errcode={errcode} errmsg={data.get('errmsg')}")
                    new_cursor = str(data.get("get_updates_buf") or "")
                    if new_cursor:
                        cursor = new_cursor
                        self._save_cursor(bot_id, cursor)
                    for msg in data.get("msgs") or []:
                        if isinstance(msg, dict):
                            self._handle_message(creds, msg)
                except requests.RequestException as exc:
                    log.warning("Weixin network retry: %s", exc)
                    time.sleep(2)
                except Exception as exc:
                    log.exception("Weixin poll error")
                    self._set_state("error", str(exc))
                    time.sleep(3)
        finally:
            self._notify(creds, False)

    def _extract_text(self, msg: dict[str, Any]) -> str:
        parts: list[str] = []
        for item in msg.get("item_list") or []:
            if isinstance(item, dict) and item.get("type") == 1:
                ti = item.get("text_item") or {}
                if isinstance(ti, dict) and ti.get("text"):
                    parts.append(str(ti["text"]))
        return "\n".join(parts).strip()

    def _download_file(self, file_item: dict[str, Any], message_id: str) -> Path:
        media = file_item.get("media") or {}
        if not isinstance(media, dict):
            raise RuntimeError("file media missing")
        enc_param = str(media.get("encrypt_query_param") or "")
        full_url = str(media.get("full_url") or "").strip()
        aes_key_value = str(media.get("aes_key") or "")
        if not aes_key_value:
            raise RuntimeError("file aes_key missing")
        url = full_url or f"{CDN_BASE_URL}/download?encrypted_query_param={quote(enc_param, safe='')}"
        resp = self.http.get(url, timeout=90)
        if resp.status_code != 200:
            raise RuntimeError(f"CDN download HTTP {resp.status_code}")
        key = decode_media_aes_key(aes_key_value)
        plaintext = unpad(AES.new(key, AES.MODE_ECB).decrypt(resp.content), 16)
        name = safe_filename(str(file_item.get("file_name") or "file.bin"))
        path = INBOX_DIR / f"{message_id[-24:]}_{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plaintext)
        return path

    def _handle_message(self, creds: dict[str, Any], msg: dict[str, Any]) -> None:
        message_id = str(msg.get("message_id") or msg.get("client_id") or msg.get("msg_id") or msg.get("id") or "")
        if not message_id or message_id in self.seen:
            return
        from_user = str(msg.get("from_user_id") or "")
        if from_user == str(creds.get("ilink_bot_id") or ""):
            self._mark_seen(message_id)
            return
        context_token = str(msg.get("context_token") or "")
        if from_user and context_token:
            self.last_context_by_user[from_user] = context_token
        text = self._extract_text(msg)
        attachments: list[Path] = []
        for item in msg.get("item_list") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == 4 and isinstance(item.get("file_item"), dict):
                try:
                    attachments.append(self._download_file(item["file_item"], message_id))
                except Exception:
                    log.exception("Inbound Weixin file download failed")
        self._mark_seen(message_id)
        inbound = InboundMessage(
            channel="weixin",
            user_id=from_user,
            message_id=message_id,
            message_type="file" if attachments else "text",
            text=text,
            attachments=attachments,
            context_token=context_token,
            received_at=utc_now(),
            raw=msg,
        )
        if self.on_message:
            try:
                self.on_message(inbound)
            except Exception:
                log.exception("Inbound message callback failed")

    def send_text(self, user_id: str, text: str, context_token: str = "") -> dict[str, Any]:
        creds = self._creds()
        if not creds:
            raise RuntimeError("微信通道未登录")
        token = context_token or self.last_context_by_user.get(user_id, "")
        client_id = f"xiaozhi:{int(time.time() * 1000)}-{secrets.token_hex(4)}"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": token,
            }
        }
        http_status, data = self._api_post(creds, "/ilink/bot/sendmessage", payload, 25, "sendMessage")
        if http_status != 200 or data.get("ret") not in (None, 0):
            raise RuntimeError(f"sendMessage failed HTTP={http_status} ret={data.get('ret')} errmsg={data.get('errmsg')}")
        return data

    def _upload_file(self, creds: dict[str, Any], file_path: Path, user_id: str) -> dict[str, Any]:
        plain = file_path.read_bytes()
        rawsize = len(plain)
        rawfilemd5 = hashlib.md5(plain).hexdigest()
        filekey = secrets.token_hex(16)
        aeskey = secrets.token_bytes(16)
        filesize = ((rawsize // 16) + 1) * 16
        _, data = self._api_post(
            creds,
            "/ilink/bot/getuploadurl",
            {
                "filekey": filekey,
                "media_type": 3,
                "to_user_id": user_id,
                "rawsize": rawsize,
                "rawfilemd5": rawfilemd5,
                "filesize": filesize,
                "no_need_thumb": True,
                "aeskey": aeskey.hex(),
            },
            30,
            "getUploadUrl",
        )
        upload_url = str(data.get("upload_full_url") or "")
        upload_param = str(data.get("upload_param") or "")
        if not upload_url and upload_param:
            upload_url = f"{CDN_BASE_URL}/upload?encrypted_query_param={quote(upload_param, safe='')}&filekey={quote(filekey, safe='')}"
        if not upload_url:
            raise RuntimeError("getUploadUrl returned no upload URL")
        cipher = AES.new(aeskey, AES.MODE_ECB).encrypt(pad(plain, 16))
        resp = self.http.post(upload_url, headers={"Content-Type": "application/octet-stream"}, data=cipher, timeout=90)
        if resp.status_code != 200:
            raise RuntimeError(f"CDN upload HTTP {resp.status_code}")
        download_param = resp.headers.get("x-encrypted-param")
        if not download_param:
            raise RuntimeError("CDN upload missing x-encrypted-param")
        return {"download_param": download_param, "aeskey_hex": aeskey.hex(), "rawsize": rawsize}

    def send_file(self, user_id: str, file_path: Path, context_token: str = "") -> dict[str, Any]:
        creds = self._creds()
        if not creds:
            raise RuntimeError("微信通道未登录")
        file_path = Path(file_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(file_path)
        uploaded = self._upload_file(creds, file_path, user_id)
        token = context_token or self.last_context_by_user.get(user_id, "")
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": f"xiaozhi:{int(time.time() * 1000)}-{secrets.token_hex(4)}",
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {
                        "type": 4,
                        "file_item": {
                            "media": {
                                "encrypt_query_param": uploaded["download_param"],
                                "aes_key": base64.b64encode(uploaded["aeskey_hex"].encode("ascii")).decode("ascii"),
                                "encrypt_type": 1,
                            },
                            "file_name": file_path.name,
                            "len": str(uploaded["rawsize"]),
                        },
                    }
                ],
                "context_token": token,
            }
        }
        http_status, data = self._api_post(creds, "/ilink/bot/sendmessage", payload, 30, "sendFileMessage")
        if http_status != 200 or data.get("ret") not in (None, 0):
            raise RuntimeError(f"sendFileMessage failed HTTP={http_status} ret={data.get('ret')} errmsg={data.get('errmsg')}")
        return data
