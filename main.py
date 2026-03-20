from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urlparse

import requests
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_DIR = Path(__file__).resolve().parent
API_CONFIG_FILE = PLUGIN_DIR / "api_123pan.txt"
BASE_URL = "https://open-api.123pan.com"

ENDPOINTS = {
    "access_token": "/api/v1/access_token",
    "user_info": "/api/v1/user/info",
    "mkdir": "/upload/v1/file/mkdir",
    "file_create": "/upload/v1/file/create",
    "list_upload_parts": "/upload/v1/file/list_upload_parts",
    "get_upload_url": "/upload/v1/file/get_upload_url",
    "upload_complete": "/upload/v1/file/upload_complete",
    "upload_async_result": "/upload/v1/file/upload_async_result",
    "move": "/api/v1/file/move",
    "trash": "/api/v1/file/trash",
    "recover": "/api/v1/file/recover",
    "delete": "/api/v1/file/delete",
    "file_list_v1": "/api/v1/file/list",
    "share_create": "/api/v1/share/create",
    "offline_download": "/api/v1/offline/download",
    "direct_enable": "/api/v1/direct-link/enable",
    "direct_disable": "/api/v1/direct-link/disable",
    "direct_url": "/api/v1/direct-link/url",
    "query_transcode": "/api/v1/direct-link/queryTranscode",
    "do_transcode": "/api/v1/direct-link/doTranscode",
    "get_m3u8": "/api/v1/direct-link/get/m3u8",
    "rename": "/api/v1/file/rename",
    "file_detail": "/api/v1/file/detail",
    "file_list_v2": "/api/v2/file/list",
}


class Pan123Error(Exception):
    """123 盘 API 调用失败。"""

@dataclass
class Pan123Settings:
    client_id: str = ""
    client_secret: str = ""
    private_key: str = ""
    access_token: str = ""
    expired_at: str = ""
    uid: int = 0
    request_timeout: int = 30
    direct_link_sign_expire_seconds: int = 86400
    prefer_v2_list: bool = True


class Pan123OpenAPI:
    def __init__(self, plugin: "Pan123Plugin"):
        self.plugin = plugin
        self.settings = plugin.load_settings()
        self.base_url = BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "Platform": "open_platform",
            "Content-Type": "application/json",
        })
        self._apply_auth_header()

    def _apply_auth_header(self) -> None:
        token = self.settings.access_token.strip()
        if token:
            self.session.headers["Authorization"] = token
        else:
            self.session.headers.pop("Authorization", None)

    def _timeout(self) -> int:
        timeout = self.settings.request_timeout
        return timeout if timeout > 0 else 30

    def _build_url(self, endpoint: str) -> str:
        return f"{self.base_url}{endpoint}"

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        auth_required: bool = True,
        timeout: int | tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        self.settings = self.plugin.load_settings()
        self._apply_auth_header()
        url = self._build_url(endpoint)
        actual_timeout = timeout if timeout is not None else self._timeout()
        try:
            if method.upper() == "GET":
                response = self.session.get(url, params=params, timeout=actual_timeout)
            else:
                payload = json_data if json_data is not None else data
                if json_data is not None:
                    response = self.session.post(url, params=params, json=payload, timeout=actual_timeout)
                else:
                    response = self.session.post(url, params=params, data=payload, timeout=actual_timeout)
        except requests.RequestException as exc:
            raise Pan123Error(f"网络请求失败：{exc}") from exc

        try:
            result = response.json()
        except ValueError as exc:
            raise Pan123Error(f"接口返回了非 JSON 内容，HTTP {response.status_code}") from exc

        code = result.get("code")
        message = result.get("message", "")
        if code == 401 or message == "token is expired":
            if auth_required:
                self.refresh_access_token()
                self._apply_auth_header()
                return self._request(
                    method,
                    endpoint,
                    params=params,
                    json_data=json_data,
                    data=data,
                    auth_required=False,
                    timeout=timeout,
                )
        if response.status_code >= 400:
            raise Pan123Error(f"HTTP {response.status_code}: {message or response.text}")
        if code not in (0, None):
            raise Pan123Error(message or f"接口返回错误码：{code}")
        return result

    def refresh_access_token(self) -> dict[str, Any]:
        self.settings = self.plugin.load_settings()
        if not self.settings.client_id or not self.settings.client_secret:
            raise Pan123Error("缺少 client_id 或 client_secret，无法刷新 access_token")
        result = self._request(
            "POST",
            ENDPOINTS["access_token"],
            data={
                "clientID": self.settings.client_id,
                "clientSecret": self.settings.client_secret,
            },
            auth_required=False,
        )
        data = result.get("data") or {}
        access_token = data.get("accessToken", "")
        expired_at = str(data.get("expiredAt", "")).replace("T", " ")[:19]
        if not access_token:
            raise Pan123Error("刷新 access_token 失败：返回中缺少 accessToken")
        self.plugin.persist_runtime_auth(access_token=access_token, expired_at=expired_at)
        self.settings = self.plugin.load_settings()
        self._apply_auth_header()
        return {
            "accessToken": access_token,
            "expiredAt": expired_at,
        }

    def user_info(self) -> dict[str, Any]:
        result = self._request("GET", ENDPOINTS["user_info"])
        return result.get("data") or {}

    def mkdir(self, name: str, parent_id: int) -> dict[str, Any]:
        result = self._request(
            "POST",
            ENDPOINTS["mkdir"],
            data={"name": name, "parentID": parent_id},
        )
        return result.get("data") or {}

    def create_file(self, file_path: str, parent_file_id: int) -> dict[str, Any]:
        size_in_bytes, file_name = self.get_file_info(file_path)
        etag = self.calculate_md5(file_path)
        result = self._request(
            "POST",
            ENDPOINTS["file_create"],
            data={
                "parentFileID": parent_file_id,
                "filename": file_name,
                "etag": etag,
                "size": size_in_bytes,
            },
        )
        payload = result.get("data") or {}
        file_id = payload.get("fileID")
        reuse = bool(payload.get("reuse"))
        if reuse:
            return {
                "fileID": file_id,
                "reuse": True,
                "completed": True,
                "message": "文件已秒传成功",
            }

        preupload_id = payload.get("preuploadID")
        slice_size = int(payload.get("sliceSize") or 0)
        if not preupload_id or slice_size <= 0:
            raise Pan123Error("创建上传任务失败：缺少 preuploadID 或 sliceSize")

        uploaded_parts = self.upload_slices(file_path, slice_size, size_in_bytes, preupload_id)
        remote_parts: list[dict[str, Any]] = []
        if size_in_bytes > slice_size:
            remote_parts = self.list_upload_parts(preupload_id)
            if remote_parts != uploaded_parts:
                raise Pan123Error("云端分片信息与本地分片信息不一致")

        complete_result = self.upload_complete(preupload_id)
        if complete_result.get("completed"):
            return {
                "fileID": complete_result.get("fileID") or file_id,
                "reuse": False,
                "completed": True,
                "async": False,
                "uploadedParts": uploaded_parts,
                "remoteParts": remote_parts,
            }

        if complete_result.get("async"):
            time.sleep(3)
            async_result = self.upload_async_result(preupload_id)
            if async_result.get("completed"):
                return {
                    "fileID": async_result.get("fileID") or file_id,
                    "reuse": False,
                    "completed": True,
                    "async": True,
                    "uploadedParts": uploaded_parts,
                    "remoteParts": remote_parts,
                }
            found_id = self.find_file_id(parent_file_id, file_name)
            if found_id:
                return {
                    "fileID": found_id,
                    "reuse": False,
                    "completed": True,
                    "async": True,
                    "uploadedParts": uploaded_parts,
                    "remoteParts": remote_parts,
                    "message": "异步查询未命中，已通过列表补偿找到文件",
                }
        raise Pan123Error("文件上传未完成")

    def get_upload_url(self, preupload_id: str, slice_no: int) -> str:
        result = self._request(
            "POST",
            ENDPOINTS["get_upload_url"],
            data={"preuploadID": preupload_id, "sliceNo": slice_no},
        )
        data = result.get("data") or {}
        presigned_url = data.get("presignedURL")
        if not presigned_url:
            raise Pan123Error("获取上传地址失败")
        return presigned_url

    def upload_slices(
        self,
        file_path: str,
        slice_size: int,
        size_in_bytes: int,
        preupload_id: str,
    ) -> list[dict[str, Any]]:
        upload_data_parts: list[dict[str, Any]] = []
        num_slices = math.ceil(size_in_bytes / slice_size)
        with open(file_path, "rb") as file_obj:
            for slice_no in range(1, num_slices + 1):
                presigned_url = self.get_upload_url(preupload_id, slice_no)
                chunk = file_obj.read(slice_size)
                chunk_md5 = hashlib.md5(chunk).hexdigest()
                try:
                    response = requests.put(presigned_url, data=chunk, timeout=self._timeout())
                except requests.RequestException as exc:
                    raise Pan123Error(f"上传分片 {slice_no} 失败：{exc}") from exc
                if response.status_code != 200:
                    raise Pan123Error(f"上传分片 {slice_no} 失败，HTTP {response.status_code}")
                upload_data_parts.append(
                    {
                        "partNumber": str(slice_no),
                        "size": len(chunk),
                        "etag": chunk_md5,
                    }
                )
        return upload_data_parts

    def list_upload_parts(self, preupload_id: str) -> list[dict[str, Any]]:
        result = self._request(
            "POST",
            ENDPOINTS["list_upload_parts"],
            data={"preuploadID": preupload_id},
        )
        data = result.get("data") or {}
        return data.get("parts") or []

    def upload_complete(self, preupload_id: str) -> dict[str, Any]:
        result = self._request(
            "POST",
            ENDPOINTS["upload_complete"],
            data={"preuploadID": preupload_id},
            timeout=(3, 10),
        )
        return result.get("data") or {}

    def upload_async_result(self, preupload_id: str) -> dict[str, Any]:
        result = self._request(
            "POST",
            ENDPOINTS["upload_async_result"],
            data={"preuploadID": preupload_id},
        )
        return result.get("data") or {}

    def move(self, file_ids: list[int], to_parent_file_id: int) -> dict[str, Any]:
        result = self._request(
            "POST",
            ENDPOINTS["move"],
            json_data={"fileIDs": file_ids, "toParentFileID": to_parent_file_id},
        )
        return result.get("data") or {}

    def trash(self, file_ids: list[int]) -> dict[str, Any]:
        result = self._request("POST", ENDPOINTS["trash"], json_data={"fileIDs": file_ids})
        return result.get("data") or {}

    def recover(self, file_ids: list[int]) -> dict[str, Any]:
        result = self._request("POST", ENDPOINTS["recover"], json_data={"fileIDs": file_ids})
        return result.get("data") or {}

    def delete(self, file_ids: list[int]) -> dict[str, Any]:
        result = self._request("POST", ENDPOINTS["delete"], json_data={"fileIDs": file_ids})
        return result.get("data") or {}

    def rename(self, rename_list: list[str]) -> dict[str, Any]:
        result = self._request("POST", ENDPOINTS["rename"], json_data={"renameList": rename_list})
        return result.get("data") or {}

    def file_detail(self, file_id: int) -> dict[str, Any]:
        result = self._request("GET", ENDPOINTS["file_detail"], params={"fileID": file_id})
        return result.get("data") or {}

    def file_list_v1(
        self,
        parent_file_id: int,
        page: int = 1,
        limit: int = 100,
        order_by: str = "createAt",
        order_direction: str = "desc",
        trashed: bool = False,
        search_data: str = "",
    ) -> dict[str, Any]:
        result = self._request(
            "GET",
            ENDPOINTS["file_list_v1"],
            params={
                "parentFileId": parent_file_id,
                "page": page,
                "limit": limit,
                "orderBy": order_by,
                "orderDirection": order_direction,
                "trashed": str(trashed).lower(),
                "searchData": search_data,
            },
        )
        return result.get("data") or {}

    def file_list_v2(
        self,
        parent_file_id: int,
        last_file_id: int = 0,
        limit: int = 100,
        search_data: str = "",
        search_mode: int = 1,
    ) -> dict[str, Any]:
        result = self._request(
            "GET",
            ENDPOINTS["file_list_v2"],
            params={
                "parentFileId": parent_file_id,
                "lastFileId": last_file_id,
                "limit": limit,
                "searchData": search_data,
                "searchMode": search_mode,
            },
        )
        return result.get("data") or {}

    def find_file_id(self, parent_file_id: int, file_name: str) -> int | None:
        for page in range(1, 6):
            data = self.file_list_v1(parent_file_id=parent_file_id, page=page, limit=100)
            for item in data.get("fileList") or []:
                if item.get("filename") == file_name:
                    file_id = item.get("fileID") or item.get("fileId")
                    if file_id is not None:
                        return int(file_id)
        return None

    def share_create(
        self,
        file_id_list: list[int],
        share_name: str,
        share_expire: int,
        share_pwd: str = "",
    ) -> dict[str, Any]:
        result = self._request(
            "POST",
            ENDPOINTS["share_create"],
            json_data={
                "shareName": share_name,
                "shareExpire": share_expire,
                "fileIDList": file_id_list,
                "sharePwd": share_pwd,
            },
        )
        data = result.get("data") or {}
        share_key = data.get("shareKey", "")
        data["shareUrl"] = f"https://www.123pan.com/s/{share_key}" if share_key else ""
        return data

    def offline_download(self, url: str, file_name: str, parent_file_id: int) -> dict[str, Any]:
        result = self._request(
            "POST",
            ENDPOINTS["offline_download"],
            json_data={
                "resourceUrl": url,
                "fileName": file_name,
                "parentFileId": parent_file_id,
            },
        )
        return result.get("data") or {}

    def direct_link_enable(self) -> dict[str, Any]:
        result = self._request("POST", ENDPOINTS["direct_enable"], json_data={})
        return result.get("data") or {}

    def direct_link_disable(self) -> dict[str, Any]:
        result = self._request("POST", ENDPOINTS["direct_disable"], json_data={})
        return result.get("data") or {}

    def direct_link_url(self, file_id: int) -> dict[str, Any]:
        result = self._request("GET", ENDPOINTS["direct_url"], params={"fileID": file_id})
        return result.get("data") or {}

    def query_transcode(self, file_id: int) -> dict[str, Any]:
        result = self._request(
            "POST",
            ENDPOINTS["query_transcode"],
            json_data={"fileID": file_id},
        )
        return result.get("data") or {}

    def do_transcode(self, file_id: int) -> dict[str, Any]:
        result = self._request(
            "POST",
            ENDPOINTS["do_transcode"],
            json_data={"fileID": file_id},
        )
        return result.get("data") or {}

    def get_m3u8(self, file_id: int) -> dict[str, Any]:
        result = self._request("GET", ENDPOINTS["get_m3u8"], params={"fileID": file_id})
        return result.get("data") or {}

    def direct_link_auth_key_url(self, direct_url: str) -> str:
        valid_duration = self.settings.direct_link_sign_expire_seconds
        if valid_duration <= 0:
            valid_duration = 86400
        signed_url, _ = self.sign_url(direct_url, self.settings.private_key, valid_duration)
        return signed_url

    @staticmethod
    def sign_url(origin_url: str, private_key: str, valid_duration: int) -> tuple[str, str]:
        if not origin_url:
            raise Pan123Error("直链 URL 不能为空")
        if not private_key:
            raise Pan123Error("private_key 未配置，无法生成鉴权直链")
        ts = int(time.time() + valid_duration)
        r_int = random.randint(0, 1000000)
        parsed_url = urlparse(origin_url)
        path = unquote(parsed_url.path)
        data_to_sign = f"{path}-{ts}-{r_int}-{private_key}"
        hashed_data = hashlib.md5(data_to_sign.encode()).hexdigest()
        query_params = {"auth_key": f"{ts}-{r_int}-{hashed_data}"}
        signed_url = f"{parsed_url.scheme}://{parsed_url.netloc}{path}?{urlencode(query_params)}"
        return signed_url, urlencode(query_params)

    def fileid_to_authurl(self, file_id: int) -> dict[str, Any]:
        direct_data = self.direct_link_url(file_id)
        direct_url = direct_data.get("url", "")
        auth_url = self.direct_link_auth_key_url(direct_url)
        return {
            "directUrl": direct_url,
            "authUrl": auth_url,
        }

    @staticmethod
    def calculate_md5(file_path: str) -> str:
        md5_hash = hashlib.md5()
        with open(file_path, "rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()

    @staticmethod
    def get_file_info(file_path: str) -> tuple[int, str]:
        file_name = os.path.basename(file_path)
        size_in_bytes = os.path.getsize(file_path)
        return size_in_bytes, file_name


@register("astrbot_plugin_123pan_save", "cycle", "123 盘 OpenAPI 插件", "1.0.0")
class Pan123Plugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.runtime_cache: dict[str, Any] = {}

    async def initialize(self):
        self._load_runtime_auth_from_file()
        logger.info("123 盘插件已初始化")

    async def terminate(self):
        logger.info("123 盘插件已卸载")

    def _safe_get_plugin_config(self) -> dict[str, Any]:
        if isinstance(getattr(self, "config", None), dict):
            return self.config
        candidate = getattr(self.context, "config", None)
        if isinstance(candidate, dict):
            return candidate
        return {}

    def _load_runtime_auth_from_file(self) -> None:
        if not API_CONFIG_FILE.exists():
            return
        try:
            raw = API_CONFIG_FILE.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"读取 api_123pan.txt 失败：{exc}")
            return
        parsed: dict[str, str] = {}
        for line in raw.splitlines():
            if not line.strip() or ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed[key.strip()] = value.strip()
        self.runtime_cache.update(
            {
                "access_token": parsed.get("access_token", ""),
                "expired_at": parsed.get("expiredAt", ""),
                "private_key": parsed.get("private_key", ""),
                "uid": self._to_int(parsed.get("uid", 0), 0),
                "client_id": parsed.get("client_123id", ""),
                "client_secret": parsed.get("client_123secret", ""),
            }
        )

    def persist_runtime_auth(
        self,
        *,
        access_token: str | None = None,
        expired_at: str | None = None,
        private_key: str | None = None,
        uid: int | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._load_runtime_auth_from_file()
        if access_token is not None:
            self.runtime_cache["access_token"] = access_token
        if expired_at is not None:
            self.runtime_cache["expired_at"] = expired_at
        if private_key is not None:
            self.runtime_cache["private_key"] = private_key
        if uid is not None:
            self.runtime_cache["uid"] = uid
        if client_id is not None:
            self.runtime_cache["client_id"] = client_id
        if client_secret is not None:
            self.runtime_cache["client_secret"] = client_secret

        content = "\n".join(
            [
                f"access_token:{self.runtime_cache.get('access_token', '')}",
                f"expiredAt:{self.runtime_cache.get('expired_at', '')}",
                f"private_key:{self.runtime_cache.get('private_key', '')}",
                f"uid:{self.runtime_cache.get('uid', 0)}",
                f"client_123id:{self.runtime_cache.get('client_id', '')}",
                f"client_123secret:{self.runtime_cache.get('client_secret', '')}",
            ]
        ) + "\n"
        try:
            API_CONFIG_FILE.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"写入 api_123pan.txt 失败：{exc}")

    def load_settings(self) -> Pan123Settings:
        config = self._safe_get_plugin_config()
        self._load_runtime_auth_from_file()
        settings = Pan123Settings(
            client_id=str(config.get("client_id") or self.runtime_cache.get("client_id") or "").strip(),
            client_secret=str(config.get("client_secret") or self.runtime_cache.get("client_secret") or "").strip(),
            private_key=str(config.get("private_key") or self.runtime_cache.get("private_key") or "").strip(),
            access_token=str(config.get("access_token") or self.runtime_cache.get("access_token") or "").strip(),
            expired_at=str(config.get("expired_at") or self.runtime_cache.get("expired_at") or "").strip(),
            uid=self._to_int(config.get("uid") or self.runtime_cache.get("uid") or 0, 0),
            request_timeout=self._to_int(config.get("request_timeout") or 30, 30),
            direct_link_sign_expire_seconds=self._to_int(
                config.get("direct_link_sign_expire_seconds") or 86400,
                86400,
            ),
            prefer_v2_list=bool(config.get("prefer_v2_list", True)),
        )
        self.persist_runtime_auth(
            access_token=settings.access_token,
            expired_at=settings.expired_at,
            private_key=settings.private_key,
            uid=settings.uid,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )
        return settings

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool_text(value: bool) -> str:
        return "是" if value else "否"

    @staticmethod
    def _json_text(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2)

    @staticmethod
    def _parse_args(message_str: str) -> list[str]:
        text = message_str.strip()
        if not text:
            return []
        try:
            args = shlex.split(text, posix=False)
        except ValueError:
            args = text.split()
        normalized = [item.strip().strip('"').strip("'") for item in args if item.strip()]
        if normalized and normalized[0].lstrip("/").lower() == "123pan":
            normalized = normalized[1:]
        return normalized

    def _client(self) -> Pan123OpenAPI:
        return Pan123OpenAPI(self)

    async def _run_blocking(self, func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    def _help_text(self) -> str:
        return "\n".join(
            [
                "123盘插件指令：",
                "/123pan help - 查看帮助",
                "/123pan token - 刷新 access_token",
                "/123pan user - 获取用户信息",
                "/123pan mkdir <目录名> [父目录ID] - 创建目录",
                "/123pan upload <本地文件路径> [父目录ID] - 上传文件",
                "/123pan list [父目录ID] [关键字] [lastFileId] [limit] - 获取文件列表(v2)",
                "/123pan listv1 [父目录ID] [页码] [limit] [关键字] [trashed] - 获取文件列表(v1)",
                "/123pan detail <fileID> - 获取文件详情",
                "/123pan move <fileIDs逗号分隔> <目标目录ID> - 移动文件",
                "/123pan trash <fileIDs逗号分隔> - 删除到回收站",
                "/123pan recover <fileIDs逗号分隔> - 从回收站恢复",
                "/123pan delete <fileIDs逗号分隔> - 彻底删除",
                "/123pan rename <fileID:新名称> [更多项...] - 批量重命名",
                "/123pan share <fileIDs逗号分隔> <分享名称> <1|7|30|0> [提取码] - 创建分享",
                "/123pan offline <URL> <保存文件名> [父目录ID] - 创建离线下载",
                "/123pan direct-enable - 启用直链空间",
                "/123pan direct-disable - 禁用直链空间",
                "/123pan direct-url <fileID> - 获取直链",
                "/123pan direct-auth <fileID> - 获取鉴权直链",
                "/123pan transcode-status <fileID> - 查询转码进度",
                "/123pan transcode-start <fileID> - 发起直链转码",
                "/123pan m3u8 <fileID> - 获取转码 m3u8 链接",
            ]
        )

    @staticmethod
    def _parse_file_ids(raw: str) -> list[int]:
        items = [item.strip() for item in raw.split(",") if item.strip()]
        if not items:
            raise Pan123Error("fileIDs 不能为空")
        return [int(item) for item in items]

    @staticmethod
    def _parse_rename_list(items: list[str]) -> list[str]:
        rename_list: list[str] = []
        for item in items:
            if ":" not in item:
                raise Pan123Error("重命名参数格式必须为 fileID:新名称")
            file_id, new_name = item.split(":", 1)
            file_id = file_id.strip()
            new_name = new_name.strip()
            if not file_id or not new_name:
                raise Pan123Error("重命名参数格式必须为 fileID:新名称")
            int(file_id)
            rename_list.append(f"{file_id}|{new_name}")
        return rename_list

    def _resolve_local_file(self, raw_path: str) -> str:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (PLUGIN_DIR / candidate).resolve()
        if not candidate.exists() or not candidate.is_file():
            raise Pan123Error(f"本地文件不存在：{candidate}")
        return str(candidate)

    @filter.command("123pan")
    async def pan123(self, event: AstrMessageEvent):
        """123 盘 OpenAPI 指令入口。发送 /123pan help 查看完整指令说明。"""
        args = self._parse_args(event.message_str)
        if not args:
            yield event.plain_result(self._help_text())
            return

        action = args[0].lower()
        client = self._client()

        try:
            if action == "help":
                yield event.plain_result(self._help_text())
                return

            if action == "token":
                data = await self._run_blocking(client.refresh_access_token)
                yield event.plain_result(
                    "access_token 刷新成功\n"
                    f"expiredAt: {data.get('expiredAt', '')}\n"
                    f"accessToken: {data.get('accessToken', '')}"
                )
                return

            if action == "user":
                data = await self._run_blocking(client.user_info)
                used = round((data.get("spaceUsed") or 0) / 1024 / 1024 / 1024, 2)
                permanent = round((data.get("spacePermanent") or 0) / 1024 / 1024 / 1024 / 1024, 2)
                yield event.plain_result(
                    "用户信息\n"
                    f"uid: {data.get('uid')}\n"
                    f"昵称: {data.get('nickname', '')}\n"
                    # f"邮箱: {data.get('mail', '')}\n"
                    # f"手机号: {data.get('passport', '')}\n"
                    f"已用空间: {used} GB\n"
                    f"永久空间: {permanent} TB\n"
                    f"临时空间: {data.get('spaceTemp', 0)}\n"
                    f"临时空间到期: {data.get('spaceTempExpr', '')}"
                )
                return

            if action == "mkdir":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan mkdir <目录名> [父目录ID]")
                name = args[1]
                parent_id = int(args[2]) if len(args) >= 3 else 0
                data = await self._run_blocking(client.mkdir, name, parent_id)
                yield event.plain_result(f"创建目录成功\ndirID: {data.get('dirID')}")
                return

            if action == "upload":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan upload <本地文件路径> [父目录ID]")
                local_file = self._resolve_local_file(args[1])
                parent_id = int(args[2]) if len(args) >= 3 else 0
                data = await self._run_blocking(client.create_file, local_file, parent_id)
                yield event.plain_result(
                    "上传成功\n"
                    f"fileID: {data.get('fileID')}\n"
                    f"秒传: {self._to_bool_text(bool(data.get('reuse')))}\n"
                    f"异步完成: {self._to_bool_text(bool(data.get('async')))}"
                )
                return

            if action == "list":
                parent_id = int(args[1]) if len(args) >= 2 else 0
                search_data = args[2] if len(args) >= 3 else ""
                last_file_id = int(args[3]) if len(args) >= 4 else 0
                limit = int(args[4]) if len(args) >= 5 else 100
                data = await self._run_blocking(client.file_list_v2, parent_id, last_file_id, limit, search_data, 1)
                yield event.plain_result(self._json_text(data))
                return

            if action == "listv1":
                parent_id = int(args[1]) if len(args) >= 2 else 0
                page = int(args[2]) if len(args) >= 3 else 1
                limit = int(args[3]) if len(args) >= 4 else 100
                search_data = args[4] if len(args) >= 5 else ""
                trashed = str(args[5]).lower() in {"1", "true", "yes", "y", "是"} if len(args) >= 6 else False
                data = await self._run_blocking(client.file_list_v1, parent_id, page, limit, "createAt", "desc", trashed, search_data)
                yield event.plain_result(self._json_text(data))
                return

            if action == "detail":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan detail <fileID>")
                file_id = int(args[1])
                data = await self._run_blocking(client.file_detail, file_id)
                yield event.plain_result(self._json_text(data))
                return

            if action == "move":
                if len(args) < 3:
                    raise Pan123Error("用法：/123pan move <fileIDs逗号分隔> <目标目录ID>")
                file_ids = self._parse_file_ids(args[1])
                target_parent_id = int(args[2])
                data = await self._run_blocking(client.move, file_ids, target_parent_id)
                yield event.plain_result(f"移动成功\n{self._json_text(data)}")
                return

            if action == "trash":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan trash <fileIDs逗号分隔>")
                file_ids = self._parse_file_ids(args[1])
                data = await self._run_blocking(client.trash, file_ids)
                yield event.plain_result(f"已移入回收站\n{self._json_text(data)}")
                return

            if action == "recover":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan recover <fileIDs逗号分隔>")
                file_ids = self._parse_file_ids(args[1])
                data = await self._run_blocking(client.recover, file_ids)
                yield event.plain_result(f"恢复成功\n{self._json_text(data)}")
                return

            if action == "delete":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan delete <fileIDs逗号分隔>")
                file_ids = self._parse_file_ids(args[1])
                data = await self._run_blocking(client.delete, file_ids)
                yield event.plain_result(f"彻底删除成功\n{self._json_text(data)}")
                return

            if action == "rename":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan rename <fileID:新名称> [更多项...]")
                rename_list = self._parse_rename_list(args[1:])
                data = await self._run_blocking(client.rename, rename_list)
                yield event.plain_result(f"重命名成功\n{self._json_text(data)}")
                return

            if action == "share":
                if len(args) < 4:
                    raise Pan123Error("用法：/123pan share <fileIDs逗号分隔> <分享名称> <1|7|30|0> [提取码]")
                file_ids = self._parse_file_ids(args[1])
                share_name = args[2]
                share_expire = int(args[3])
                share_pwd = args[4] if len(args) >= 5 else ""
                data = await self._run_blocking(client.share_create, file_ids, share_name, share_expire, share_pwd)
                yield event.plain_result(
                    "创建分享成功\n"
                    f"shareKey: {data.get('shareKey', '')}\n"
                    f"shareUrl: {data.get('shareUrl', '')}\n"
                    f"sharePwd: {data.get('sharePwd', share_pwd)}"
                )
                return

            if action == "offline":
                if len(args) < 3:
                    raise Pan123Error("用法：/123pan offline <URL> <保存文件名> [父目录ID]")
                url = args[1]
                file_name = args[2]
                parent_id = int(args[3]) if len(args) >= 4 else 0
                data = await self._run_blocking(client.offline_download, url, file_name, parent_id)
                yield event.plain_result(f"离线下载任务已创建\n{self._json_text(data)}")
                return

            if action == "direct-enable":
                data = await self._run_blocking(client.direct_link_enable)
                yield event.plain_result(f"已启用直链空间\n{self._json_text(data)}")
                return

            if action == "direct-disable":
                data = await self._run_blocking(client.direct_link_disable)
                yield event.plain_result(f"已禁用直链空间\n{self._json_text(data)}")
                return

            if action == "direct-url":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan direct-url <fileID>")
                file_id = int(args[1])
                data = await self._run_blocking(client.direct_link_url, file_id)
                yield event.plain_result(
                    "直链获取成功\n"
                    f"url: {data.get('url', '')}\n"
                    f"raw: {self._json_text(data)}"
                )
                return

            if action == "direct-auth":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan direct-auth <fileID>")
                file_id = int(args[1])
                data = await self._run_blocking(client.fileid_to_authurl, file_id)
                yield event.plain_result(
                    "鉴权直链生成成功\n"
                    f"directUrl: {data.get('directUrl', '')}\n"
                    f"authUrl: {data.get('authUrl', '')}"
                )
                return

            if action == "transcode-status":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan transcode-status <fileID>")
                file_id = int(args[1])
                data = await self._run_blocking(client.query_transcode, file_id)
                yield event.plain_result(self._json_text(data))
                return

            if action == "transcode-start":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan transcode-start <fileID>")
                file_id = int(args[1])
                data = await self._run_blocking(client.do_transcode, file_id)
                yield event.plain_result(f"已发起转码\n{self._json_text(data)}")
                return

            if action == "m3u8":
                if len(args) < 2:
                    raise Pan123Error("用法：/123pan m3u8 <fileID>")
                file_id = int(args[1])
                data = await self._run_blocking(client.get_m3u8, file_id)
                yield event.plain_result(self._json_text(data))
                return

            raise Pan123Error("未知子命令，请使用 /123pan help 查看帮助")
        except Pan123Error as exc:
            yield event.plain_result(f"123盘操作失败：{exc}")
        except Exception as exc:
            logger.exception("123 盘插件执行异常", exc_info=exc)
            yield event.plain_result(f"123盘操作异常：{exc}")
