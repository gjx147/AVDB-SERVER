"""CloudDrive2 gRPC-Web 协议层 + 业务封装。

从 routers/downloaders.py 提取，供 downloaders.py 和 services/cd2_organize.py 共用，
避免 router ↔ service 双向依赖。

CD2 使用纯 gRPC（非 REST），web UI 通过 gRPC-Web 网关调用。
端点：POST /clouddrive.CloudDriveFileSrv/{method}
Content-Type: application/grpc-web+proto
鉴权：Bearer token（从 GetToken 获取，或直接配置 API token）

响应由 gRPC-Web 帧封装：flag(1B) + length(4B BE) + payload。
- 数据帧 flag=0x00
- trailer 帧 flag=0x80，含 `grpc-status:N` 文本
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("avdb.downloaders.cd2")


# ── protobuf 编码 ─────────────────────────────────────────────

def encode_string(field_num: int, value: str) -> bytes:
    """编码 protobuf string 字段（wire_type=2, length-delimited）。

    格式: tag_byte + varint_length + utf8_bytes
    tag = (field_number << 3) | 2
    """
    tag = (field_num << 3) | 2
    data = value.encode("utf-8")
    # varint 编码长度
    length_bytes = []
    v = len(data)
    while v > 0:
        length_bytes.append(v & 0x7F)
        v >>= 7
    if not length_bytes:
        length_bytes = [0]
    length_bytes = bytes([(b | 0x80) for b in length_bytes[:-1]] + [length_bytes[-1]])
    return bytes([tag]) + length_bytes + data


def encode_varint(field_num: int, value: int) -> bytes:
    """编码 protobuf varint 字段（wire_type=0，用于 bool/int/enum）。"""
    tag = (field_num << 3) | 0
    v = value
    out = []
    while v > 0:
        out.append(v & 0x7F)
        v >>= 7
    if not out:
        out = [0]
    return bytes([tag]) + bytes([(b | 0x80) for b in out[:-1]] + [out[-1]])


# ── gRPC-Web 帧封装/解析 ──────────────────────────────────────

def grpc_web_frame(payload: bytes) -> bytes:
    """构造 gRPC-Web 帧：flag(1b, 0) + length(4b BE) + payload。"""
    return b"\x00" + len(payload).to_bytes(4, "big") + payload


def parse_grpc_web_response(body: bytes) -> tuple[bytes, str]:
    """解析 gRPC-Web 响应。返回 (data_payload, grpc_status)。

    响应由多个帧组成：
    - 数据帧: flag=0x00, 含 protobuf payload（流式 RPC 的多个数据帧会合并）
    - trailer 帧: flag=0x80, 含 grpc-status:N 文本
    """
    data = b""
    grpc_status = ""
    i = 0
    while i + 5 <= len(body):
        flag = body[i]
        length = int.from_bytes(body[i + 1:i + 5], "big")
        if i + 5 + length > len(body):
            break
        chunk = body[i + 5:i + 5 + length]
        if flag & 0x80:
            text = chunk.decode("utf-8", errors="replace")
            for line in text.split("\r\n"):
                if line.startswith("grpc-status:"):
                    grpc_status = line.split(":", 1)[1].strip()
        else:
            data += chunk
        i += 5 + length
    return data, grpc_status


async def grpc_web_call(base: str, method: str, payload: bytes, token: str = "") -> tuple[bytes, str, int]:
    """调用 CD2 gRPC-Web 端点。返回 (data, grpc_status, http_status)。"""
    frame = grpc_web_frame(payload)
    headers = {"Content-Type": "application/grpc-web+proto", "X-Grpc-Web": "1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = base.rstrip("/") + "/clouddrive.CloudDriveFileSrv/" + method
    async with httpx.AsyncClient(timeout=30, verify=False) as c:
        r = await c.post(url, content=frame, headers=headers)
        data, grpc_status = parse_grpc_web_response(r.content)
        if not grpc_status:
            grpc_status = r.headers.get("grpc-status", "")
        return data, grpc_status, r.status_code


# ── protobuf 解析（按字段提取，简单实现，不处理嵌套） ─────────

def _iter_fields(data: bytes):
    """生成器：遍历顶层 protobuf 字段，yield (field_num, wire_type, value_bytes/offset)。

    对于 wire_type=2 (length-delimited)，value 为完整的字段字节（含嵌套 message）。
    对于 wire_type=0 (varint)，value 为 int 值。
    """
    i = 0
    n = len(data)
    while i < n:
        # 读 tag（varint，可能多字节，但 field_num 一般 < 16 单字节）
        tag = 0
        shift = 0
        while i < n:
            b = data[i]
            i += 1
            tag |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        wire_type = tag & 0x07
        fn = tag >> 3
        if wire_type == 2:  # length-delimited
            length = 0
            shift = 0
            while i < n:
                b = data[i]
                i += 1
                length |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            value = data[i:i + length]
            i += length
            yield fn, wire_type, value
        elif wire_type == 0:  # varint
            v = 0
            shift = 0
            while i < n:
                b = data[i]
                i += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            yield fn, wire_type, v
        elif wire_type == 5:  # 32-bit
            yield fn, wire_type, data[i:i + 4]
            i += 4
        elif wire_type == 1:  # 64-bit
            yield fn, wire_type, data[i:i + 8]
            i += 8
        else:
            break


def extract_string_field(data: bytes, field_num: int) -> str:
    """从 protobuf 二进制中提取指定 string 字段（返回第一个匹配）。"""
    for fn, wt, v in _iter_fields(data):
        if fn == field_num and wt == 2:
            return v.decode("utf-8", errors="replace")
    return ""


def extract_repeated_string_field(data: bytes, field_num: int) -> list[str]:
    """提取 repeated string 字段（所有匹配）。"""
    out = []
    for fn, wt, v in _iter_fields(data):
        if fn == field_num and wt == 2:
            out.append(v.decode("utf-8", errors="replace"))
    return out


def extract_bool_field(data: bytes, field_num: int) -> bool:
    """提取 bool/varint 字段。"""
    for fn, wt, v in _iter_fields(data):
        if fn == field_num and wt == 0:
            return bool(v)
    return False


def extract_int_field(data: bytes, field_num: int) -> int:
    """提取 int 字段。"""
    for fn, wt, v in _iter_fields(data):
        if fn == field_num and wt == 0:
            return int(v)
    return 0


def extract_nested_messages(data: bytes, field_num: int) -> list[bytes]:
    """提取 repeated 嵌套 message 字段（返回每个嵌套 message 的原始字节）。
    用于解析 SubFilesReply.subFiles (field1=repeated CloudDriveFile)。
    """
    out = []
    for fn, wt, v in _iter_fields(data):
        if fn == field_num and wt == 2 and isinstance(v, (bytes, bytearray)):
            out.append(bytes(v))
    return out


# ── 业务封装：鉴权 ────────────────────────────────────────────

async def login(base: str, username: str, password: str) -> tuple[str, str]:
    """GetToken 登录。返回 (token, error_msg)；error_msg 非空表示失败。"""
    if not base:
        return "", "CloudDrive2 未配置 url"
    if not username or not password:
        return "", "CloudDrive2 无 token 且无用户名密码"
    try:
        logger.info(f"CD2 登录: 用户名={username}")
        # GetTokenRequest: field1=userName, field2=password
        login_payload = encode_string(1, username) + encode_string(2, password)
        data, gstatus, _ = await grpc_web_call(base, "GetToken", login_payload)
        logger.info(f"CD2 登录响应: grpc-status={gstatus}, data_len={len(data)}")
        if gstatus == "0" and len(data) > 2:
            # JWTToken: field3=token (string)
            token = extract_string_field(data, 3)
            if not token:
                logger.error(f"CD2 登录成功但未提取到 token，raw data={data[:80]!r}")
                return "", "CloudDrive2 登录成功但未返回 token"
            logger.info("CD2 登录成功，已获取 token")
            return token, ""
        return "", f"CloudDrive2 登录失败 (grpc-status={gstatus})"
    except Exception as e:
        return "", f"CloudDrive2 登录异常: {e}"


async def get_token_or_login(config: dict) -> tuple[str, str]:
    """优先 config['clouddrive_token']，否则 username/password 登录。

    返回 (token, error_msg)；error_msg 非空表示失败。
    """
    token = config.get("clouddrive_token", "") or ""
    if token:
        return token, ""
    return await login(
        config.get("clouddrive_url", ""),
        config.get("clouddrive_username", ""),
        config.get("clouddrive_password", ""),
    )


# ── 业务封装：文件操作 ────────────────────────────────────────

async def list_folder(base: str, token: str, path: str) -> tuple[list[dict], str]:
    """GetSubFiles 列目录。返回 (files, error_msg)。

    files: [{"name", "full_path", "is_directory", "size"}]
    error_msg 非空表示失败。

    ListSubFileRequest: field1=path(string), field2=forceRefresh(bool)
    响应 SubFilesReply: field1=repeated CloudDriveFile
    CloudDriveFile: field2=name, field3=fullPathName, field4=size(int64), field30=isDirectory(bool)
    """
    payload = encode_string(1, path)
    try:
        data, gstatus, _ = await grpc_web_call(base, "GetSubFiles", payload, token)
        if gstatus != "0":
            return [], f"GetSubFiles grpc-status={gstatus}"
        files = []
        for sub_bytes in extract_nested_messages(data, 1):  # 每个 CloudDriveFile
            name = extract_string_field(sub_bytes, 2)
            full_path = extract_string_field(sub_bytes, 3)
            size = extract_int_field(sub_bytes, 4)
            is_dir = extract_bool_field(sub_bytes, 30)
            files.append({
                "name": name,
                "full_path": full_path,
                "is_directory": is_dir,
                "size": size,
            })
        return files, ""
    except Exception as e:
        return [], f"GetSubFiles 异常: {e}"


async def create_folder(base: str, token: str, parent_path: str, folder_name: str) -> tuple[bool, str]:
    """CreateFolder。幂等（已存在不视为错误）。

    CreateFolderRequest: field1=parentPath, field2=folderName
    响应 CreateFolderResult: field2=FileOperationResult
    FileOperationResult: field1=success(bool), field2=errorMessage
    """
    payload = encode_string(1, parent_path) + encode_string(2, folder_name)
    try:
        data, gstatus, _ = await grpc_web_call(base, "CreateFolder", payload, token)
        if gstatus != "0":
            # 已存在通常返回特定 grpc-status，不视为失败
            return True, f"grpc-status={gstatus}（可能已存在）"
        # CreateFolderResult 的 FileOperationResult 在 field2
        for sub_bytes in extract_nested_messages(data, 2):
            success = extract_bool_field(sub_bytes, 1)
            err = extract_string_field(sub_bytes, 2)
            if success:
                return True, ""
            # errorMessage 非空但通常表示"已存在"，不视为硬失败
            if err:
                return True, f"CreateFolder: {err}（视为已存在）"
        return True, ""
    except Exception as e:
        return False, f"CreateFolder 异常: {e}"


async def move_file(base: str, token: str, file_paths: list[str], dest_path: str) -> tuple[bool, str]:
    """MoveFile。repeated string theFilePaths(field1) + destPath(field2) + ConflictPolicy(field3)。

    ConflictPolicy: Overwrite=0, Rename=1, Skip=2。默认 Skip（避免覆盖同名）。
    返回 (success, message)。

    响应 FileOperationResult: field1=success(bool), field2=errorMessage, field3=resultFilePaths
    """
    if not file_paths:
        return False, "无文件可移动"
    # repeated string: 每个路径单独 encode_string(field_num=1)
    payload = b"".join(encode_string(1, p) for p in file_paths)
    payload += encode_string(2, dest_path)
    payload += encode_varint(3, 2)  # ConflictPolicy=Skip
    try:
        data, gstatus, _ = await grpc_web_call(base, "MoveFile", payload, token)
        if gstatus != "0":
            err_map = {"2": "未知错误", "7": "权限不足", "12": "方法不存在",
                       "13": "内部错误", "16": "未认证（token 无效或过期）"}
            return False, f"MoveFile {err_map.get(gstatus, f'grpc-status={gstatus}')}"
        success = extract_bool_field(data, 1)
        err = extract_string_field(data, 2)
        if success:
            return True, "已移动"
        return False, err or "MoveFile 返回失败但无错误信息"
    except Exception as e:
        return False, f"MoveFile 异常: {e}"


async def add_offline_files(base: str, token: str, magnet: str, save_path: str) -> tuple[bool, str]:
    """AddOfflineFiles（CD2 磁力推送，从 downloaders._push_clouddrive 提取）。

    AddOfflineFileRequest: field1=urls, field2=toFolder
    响应 FileOperationResult
    """
    payload = encode_string(1, magnet) + encode_string(2, save_path)
    try:
        data, gstatus, _ = await grpc_web_call(base, "AddOfflineFiles", payload, token)
        if gstatus == "0":
            return True, "已推送到 CloudDrive2"
        err_map = {"2": "未知错误", "7": "权限不足", "12": "方法不存在",
                   "13": "内部错误", "16": "未认证（token 无效或过期）"}
        return False, f"CloudDrive2: {err_map.get(gstatus, f'gRPC status={gstatus}')}"
    except Exception as e:
        return False, f"连接失败: {e}"
