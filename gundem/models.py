from datetime import date, datetime
from typing import Literal

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SourceKind = Literal["rss", "trends", "x"]
Lang = Literal["tr", "en"]
TopicStatus = Literal["new", "claimed", "shared", "skipped"]
DraftKind = Literal["fast", "full"]
WatchRole = Literal["breaking", "rival", "own"]
SpendJob = Literal["breaking", "rival", "velocity", "lookup"]
InteractionAction = Literal[
    "view", "copy", "edit", "verify", "ack", "claim", "shared", "skip", "split", "redraft"
]


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _ts(nullable: bool = True):
    return mapped_column(DateTime(timezone=True), nullable=nullable)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    name: Mapped[str] = mapped_column(String(200))
    org: Mapped[str] = mapped_column(String(200))
    kind: Mapped[SourceKind] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(Text)
    tier: Mapped[int] = mapped_column(Integer)
    lang: Mapped[Lang] = mapped_column(String(5))
    is_breaking_feed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql_text("true"))
    etag: Mapped[str | None] = mapped_column(String(500))
    last_modified: Mapped[str | None] = mapped_column(String(500))
    last_ok_at: Mapped[datetime | None] = _ts()
    last_error: Mapped[str | None] = mapped_column(Text)


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    title: Mapped[str] = mapped_column(Text)
    lang: Mapped[Lang] = mapped_column(String(5))
    first_seen_at: Mapped[datetime] = _ts(nullable=False)
    last_seen_at: Mapped[datetime] = _ts(nullable=False)
    is_breaking: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    score: Mapped[int] = mapped_column(Integer, default=0, server_default=sql_text("0"))
    category: Mapped[str | None] = mapped_column(String(100))
    fit_score: Mapped[int | None] = mapped_column(Integer)
    x_queries: Mapped[list] = mapped_column(JSON, default=list)
    x_velocity: Mapped[float] = mapped_column(Float, default=0.0, server_default=sql_text("0"))
    x_velocity_prev: Mapped[float] = mapped_column(Float, default=0.0, server_default=sql_text("0"))
    x_velocity_measured_at: Mapped[datetime | None] = _ts()
    trends_hit: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    org_count: Mapped[int] = mapped_column(Integer, default=0, server_default=sql_text("0"))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    verified_by: Mapped[str | None] = mapped_column(String(100))
    verified_at: Mapped[datetime | None] = _ts()
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    sensitive_ack_by: Mapped[str | None] = mapped_column(String(100))
    sensitive_ack_at: Mapped[datetime | None] = _ts()
    banned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    rival_handle: Mapped[str | None] = mapped_column(String(100))
    rival_at: Mapped[datetime | None] = _ts()
    rival_text: Mapped[str | None] = mapped_column(Text)
    rival_eng_per_min: Mapped[float] = mapped_column(Float, default=0.0, server_default=sql_text("0"))
    status: Mapped[TopicStatus] = mapped_column(String(20), default="new", server_default="new")
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = _ts()
    shared_at: Mapped[datetime | None] = _ts()
    skip_reason: Mapped[str | None] = mapped_column(Text)
    retracted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    draft_error: Mapped[str | None] = mapped_column(Text)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = _ts()
    first_seen_at: Mapped[datetime] = _ts(nullable=False)
    lang: Mapped[Lang] = mapped_column(String(5))
    is_breaking: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"))


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    kind: Mapped[DraftKind] = mapped_column(String(10))
    # [{angle, text, truncated, flags: [str]}]
    x_variants: Mapped[list] = mapped_column(JSON, default=list)
    # {cover, caption, hashtags, slides: [str]}
    ig: Mapped[dict] = mapped_column(JSON, default=dict)
    similarity: Mapped[float] = mapped_column(Float, default=0.0, server_default=sql_text("0"))


class WatchAccount(Base):
    __tablename__ = "watch_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    handle: Mapped[str] = mapped_column(String(100))
    role: Mapped[WatchRole] = mapped_column(String(20))
    tier: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql_text("true"))


class RivalPost(Base):
    __tablename__ = "rival_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    handle: Mapped[str] = mapped_column(String(100))
    tweet_id: Mapped[str] = mapped_column(String(50), unique=True)
    text: Mapped[str] = mapped_column(Text)
    created_at_x: Mapped[datetime] = _ts(nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, server_default=sql_text("0"))
    repost_count: Mapped[int] = mapped_column(Integer, default=0, server_default=sql_text("0"))
    reply_count: Mapped[int] = mapped_column(Integer, default=0, server_default=sql_text("0"))
    quote_count: Mapped[int] = mapped_column(Integer, default=0, server_default=sql_text("0"))
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"))


class XSpend(Base):
    __tablename__ = "x_spend"
    __table_args__ = (UniqueConstraint("day", "job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    day: Mapped[date] = mapped_column(Date)
    job: Mapped[SpendJob] = mapped_column(String(20))
    reads: Mapped[int] = mapped_column(Integer, default=0, server_default=sql_text("0"))


class XCursor(Base):
    __tablename__ = "x_cursors"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    job: Mapped[str] = mapped_column(String(50), unique=True)
    since_id: Mapped[str | None] = mapped_column(String(50))


class BanKeyword(Base):
    __tablename__ = "ban_keywords"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    phrase: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql_text("true"))
    note: Mapped[str | None] = mapped_column(Text)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[str | None] = mapped_column(Text)


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    editor: Mapped[str] = mapped_column(String(100))
    action: Mapped[InteractionAction] = mapped_column(String(20))
    variant: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
