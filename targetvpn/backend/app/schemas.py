from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    init_data: str = Field(alias="initData")
    start_param: str | None = None

    model_config = {"populate_by_name": True}


class AuthResponse(BaseModel):
    token: str
    user: "UserOut"


class UserOut(BaseModel):
    tg_id: int
    username: str | None = None
    first_name: str | None = None
    role: str
    is_banned: bool = False
    trial_used: bool = False
    referrals: int = 0
    ref_link: str = ""


class PlanOut(BaseModel):
    id: int
    code: str
    title: str
    description: str
    emoji: str
    price_rub: float
    old_price_rub: float | None = None
    duration_hours: int
    devices: int
    traffic_gb: int
    is_trial: bool
    is_popular: bool
    is_active: bool
    sort_order: int
    price_stars: int = 0
    price_crypto: float = 0.0


class SubscriptionOut(BaseModel):
    id: int
    plan_title: str
    devices: int
    devices_used: int
    traffic_gb: int
    expires_at: datetime
    seconds_left: int
    is_trial: bool


class DeviceOut(BaseModel):
    id: int
    name: str
    platform: str
    node_title: str = ""
    node_flag: str = ""
    config_url: str
    used_traffic_gb: float
    is_active: bool
    created_at: datetime


class StateOut(BaseModel):
    user: UserOut
    subscription: SubscriptionOut | None = None
    devices: list[DeviceOut] = []
    sub_url: str = ""
    support_url: str = ""
    trial_available: bool = False
    nodes_ready: bool = False
    payment_methods: list[str] = []


class DeviceCreate(BaseModel):
    name: str = "Устройство"
    platform: str = "other"
    node_id: int | None = None


class NodeOut(BaseModel):
    id: int
    code: str
    title: str
    flag: str
    country: str
    is_default: bool


class NodeAdminOut(NodeOut):
    url: str
    username: str
    verify_ssl: bool
    inbounds_json: str
    is_active: bool
    sort_order: int
    devices: int = 0
    online: bool | None = None


class NodeUpsert(BaseModel):
    id: int | None = None
    code: str
    title: str
    flag: str = "🌍"
    country: str = ""
    url: str
    username: str
    password: str = ""
    verify_ssl: bool = True
    inbounds_json: str = '{"vless": ["VLESS TCP REALITY"]}'
    is_active: bool = True
    is_default: bool = False
    sort_order: int = 0


class PurchaseRequest(BaseModel):
    plan_id: int
    method: str = "stars"  # stars | cryptobot | lzt
    promo_code: str = ""


class PurchaseResponse(BaseModel):
    payment_id: int
    method: str
    invoice_url: str = ""
    invoice_link: str = ""
    amount_rub: float = 0
    amount_native: float = 0
    currency: str = "RUB"
    comment: str = ""
    activated: bool = False


class PromoCheck(BaseModel):
    code: str
    plan_id: int


# --- Админка ---

class PlanUpsert(BaseModel):
    id: int | None = None
    code: str
    title: str
    description: str = ""
    emoji: str = "🚀"
    price_rub: float = 0
    old_price_rub: float | None = None
    duration_hours: int = 720
    devices: int = 1
    traffic_gb: int = 0
    is_trial: bool = False
    is_active: bool = True
    is_popular: bool = False
    sort_order: int = 0


class AdminUserOut(BaseModel):
    tg_id: int
    username: str | None
    first_name: str | None
    role: str
    is_banned: bool
    ban_reason: str | None = None
    trial_used: bool
    devices: int
    plan_title: str | None = None
    expires_at: datetime | None = None
    created_at: datetime


class GrantRequest(BaseModel):
    tg_id: int
    plan_id: int | None = None
    hours: int | None = None
    devices: int | None = None
    title: str | None = None


class BanRequest(BaseModel):
    tg_id: int
    banned: bool = True
    reason: str = ""


class RoleRequest(BaseModel):
    tg_id: int
    role: str  # user | admin


class BroadcastRequest(BaseModel):
    text: str
    only_active: bool = False


class PromoUpsert(BaseModel):
    id: int | None = None
    code: str
    discount_percent: int = 0
    bonus_days: int = 0
    max_uses: int = 0
    is_active: bool = True


class StatsOut(BaseModel):
    users_total: int
    users_active: int
    users_banned: int
    subs_active: int
    trials_active: int
    devices_active: int
    revenue_total: float
    revenue_month: float
    payments_total: int
    new_users_today: int
    node_online: bool
    nodes_total: int = 0
    nodes_online: int = 0


AuthResponse.model_rebuild()
