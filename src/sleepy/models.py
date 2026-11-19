"""Validated management configuration and server-side device adapters."""

from abc import ABC
from functools import lru_cache
from ipaddress import ip_network
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    create_model,
    field_validator,
    model_validator,
)

DeviceKind = Literal["phone", "tablet", "laptop", "desktop", "server"]
System = Literal["windows", "android", "linux"]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Text = Annotated[str, Field(max_length=512)]
CUSTOM_TYPES = {
    "text": Text,
    "number": Annotated[float, Field(allow_inf_nan=False)],
    "boolean": bool,
}
REPORT_FIELDS = {
    "using": (bool | None, None),
    "status": (Annotated[str, Field(max_length=512)] | None, None),
    "battery_percent": (Annotated[int, Field(ge=0, le=100)] | None, None),
    "charging": (bool | None, None),
    "cpu_percent": (Percent | None, None),
    "gpu_percent": (Percent | None, None),
    "memory_percent": (Percent | None, None),
}


class DeviceHints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    in_use: str = Field(default="使用中", max_length=80)
    not_using: str = Field(default="未在使用", max_length=80)
    offline: str = Field(default="不知道在干嘛喵", max_length=80)
    device_slice: int = Field(default=50, ge=0, le=512)


class CustomField(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    name: str = Field(min_length=1, max_length=40)
    type: Literal["text", "number", "boolean"] = "text"

    @field_validator("key")
    @classmethod
    def valid_key(cls, key):
        reserved = {"id", "type", "show_name", "field", "fields", "last_updated", "constructor"}
        if (
            key in REPORT_FIELDS
            or key in reserved
            or key in (DeviceConfig.model_fields.keys() - {"enabled_fields"})
        ):
            raise ValueError("字段标识与预设字段或设备资料冲突")
        return key


class DeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    kind: DeviceKind = "desktop"
    os: System = "windows"
    enabled: bool = True
    hidden: bool = False
    order: int = Field(default=0, ge=-10000, le=10000)
    timeout: int = Field(default=180, ge=10, le=86400)
    participates: bool | None = None
    hints: DeviceHints | None = None
    custom_fields: list[CustomField] = Field(default_factory=list, max_length=32)
    enabled_fields: list[str] | None = None
    receive_fields: list[str] | None = None
    public_fields: list[str] = Field(default_factory=lambda: ["using"])

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        if not value.strip():
            raise ValueError("设备名称不能为空")
        return value.strip()

    @model_validator(mode="after")
    def defaults_and_visibility(self):
        if self.participates is None:
            self.participates = self.kind != "server"
        keys = {field.key for field in self.custom_fields}
        if len(keys) != len(self.custom_fields):
            raise ValueError("该设备已有同名字段标识")
        if self.enabled_fields is None:
            if self.receive_fields is None:
                self.receive_fields = ["using", "status"]
                if self.kind in ("phone", "tablet", "laptop"):
                    self.receive_fields += ["battery_percent", "charging"]
            self.enabled_fields = sorted(keys | set(self.receive_fields))
        else:
            self.enabled_fields = sorted(set(self.enabled_fields))
            if self.receive_fields is None:
                self.receive_fields = []
            if "public_fields" not in self.model_fields_set:
                self.public_fields = []
        if not set(self.enabled_fields) <= keys | REPORT_FIELDS.keys():
            raise ValueError("只能启用预设字段或本设备定义的字段")
        self.receive_fields = sorted(set(self.receive_fields))
        self.public_fields = sorted(set(self.public_fields))
        if not set(self.receive_fields) <= set(self.enabled_fields):
            raise ValueError("接收字段必须属于已启用字段")
        if not set(self.public_fields) <= set(self.receive_fields):
            raise ValueError("公开字段必须属于接收字段")
        return self


class MusicReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    playback: Literal["playing", "paused", "idle"]
    title: str = Field(default="", max_length=512)
    artist: str = Field(default="", max_length=512)
    album: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def track(self):
        if self.playback == "idle":
            self.title = self.artist = self.album = ""
        elif not self.title:
            raise ValueError("播放或暂停时需要曲名")
        return self


class StatusLabel(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=200)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


def default_labels():
    return {
        "unknown": StatusLabel(name="探头中", description="让我看看人还在不在", color="#8b929b"),
        "active": StatusLabel(name="正在使用", description="抓到一只正在敲键盘的", color="#448b70"),
        "reachable": StatusLabel(name="活着", description="设备替我报了个平安", color="#5588b1"),
        "idle": StatusLabel(name="暂时离开", description="椅子还热，人溜了", color="#c39146"),
        "offline": StatusLabel(name="离线", description="好像有一点四了", color="#8b929b"),
        "busy": StatusLabel(name="专注中", description="等我把脑子里的进度条跑完", color="#a06862"),
        "sleeping": StatusLabel(name="睡觉中", description="本人已进入省电模式", color="#8473aa"),
    }


class DisplaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    empty_devices: str = Field(default="暂无公开设备", max_length=200)
    background_url: HttpUrl | Literal[""] = Field(
        default="https://t.alcy.cc/fj", validate_default=True
    )
    card_opacity: int = Field(default=90, ge=0, le=100)
    footer: str = Field(default="", max_length=300)
    link_text: str = Field(default="", max_length=80)
    link_url: HttpUrl | Literal[""] = ""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="似了吗？", min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    theme: Literal["system", "light", "dark"] = "system"
    display: DisplaySettings = Field(default_factory=DisplaySettings)
    hide_all: bool = False
    using_first: bool = True
    timezone: str = "Asia/Shanghai"
    stats_categories: list[
        Literal["page", "public_api", "admin_api", "report", "sse", "unmatched"]
    ] = Field(
        default_factory=lambda: ["page", "public_api", "admin_api", "report", "sse", "unmatched"]
    )
    host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    port: int = Field(default=8080, ge=1, le=65535)
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    trusted_proxies: list[str] = Field(default_factory=list)
    labels: dict[str, StatusLabel] = Field(default_factory=default_labels)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("无效的 IANA 时区") from None
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def valid_proxies(cls, value):
        for item in value:
            ip_network(item, strict=False)
        return value

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, value):
        if (
            not {"unknown", "active", "reachable", "idle", "offline", "busy"} <= value.keys()
            or len(value) > 30
        ):
            raise ValueError("必须保留默认状态，状态总数不能超过 30")
        if any(not k.isidentifier() or len(k) > 32 for k in value):
            raise ValueError("状态标识必须是最多 32 位的标识符")
        return value

    @model_validator(mode="after")
    def tls_pair(self):
        if bool(self.ssl_certfile) != bool(self.ssl_keyfile):
            raise ValueError("证书和私钥必须同时配置")
        return self


@lru_cache(maxsize=32)
def report_model(fields: tuple[str, ...], custom: tuple[tuple[str, str], ...]):
    definitions = {name: REPORT_FIELDS[name] for name in fields if name in REPORT_FIELDS}
    # Use internal names so arbitrary wire keys cannot shadow Pydantic methods.
    for index, (key, kind) in enumerate(custom):
        definitions[f"custom_{index}"] = (CUSTOM_TYPES[kind] | None, Field(default=None, alias=key))
    return create_model(
        "DeviceReport", __config__=ConfigDict(extra="ignore", strict=True), **definitions
    )


class DeviceAdapter(ABC):
    """Only instantiated on the server. Clients send ordinary flat JSON."""

    def __init__(self, config: DeviceConfig):
        self.config = config

    def validate(self, payload: object) -> dict:
        fields = tuple(self.config.receive_fields)
        custom = tuple((f.key, f.type) for f in self.config.custom_fields if f.key in fields)
        return report_model(fields, custom).model_validate(payload).model_dump(by_alias=True)


class Phone(DeviceAdapter):
    pass


class Tablet(DeviceAdapter):
    pass


class Laptop(DeviceAdapter):
    pass


class Desktop(DeviceAdapter):
    pass


class Server(DeviceAdapter):
    pass


ADAPTERS = {
    "phone": Phone,
    "tablet": Tablet,
    "laptop": Laptop,
    "desktop": Desktop,
    "server": Server,
}


def device_adapter(config: DeviceConfig) -> DeviceAdapter:
    return ADAPTERS[config.kind](config)
