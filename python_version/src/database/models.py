from datetime import datetime, timezone
from enum import IntEnum
from typing import List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SearchProviderEnum(IntEnum):
    UNKNOWN = -99
    GITHUB = 1
    GITLAB = 2
    BITBUCKET = 3
    SOURCEGRAPH = 4
    PASTEBIN = 5
    TERMBIN = 6
    GITEE = 8
    CODEBERG = 9
    GIST = 10
    NPM = 11
    PYPI = 12
    DOCKERHUB = 13
    GOOGLE_DORK = 14
    BING_DORK = 15
    INTELLIGENCE_X = 16
    PUBLIC_WWW = 17
    SOURCEFORGE = 18
    LAUNCHPAD = 19
    GHOSTBIN = 20
    HASTEBIN = 21


class ApiStatusEnum(IntEnum):
    UNVERIFIED = -99
    VALID = 1
    INVALID = 0
    REMOVED = 3
    FLAGGED_FOR_REMOVAL = 4
    NO_LONGER_WORKING = 5
    ERROR = 6
    VALID_NO_CREDITS = 7


class ApiTypeEnum(IntEnum):
    UNKNOWN = -99
    OPENAI = 100
    AZURE_OPENAI = 110
    ANTHROPIC_CLAUDE = 120
    GOOGLE_AI = 130
    COHERE = 140
    HUGGINGFACE = 150
    STABILITY_AI = 160
    MISTRAL_AI = 170
    REPLICATE = 180
    TOGETHER_AI = 190
    OPENROUTER = 195
    PERPLEXITY_AI = 196
    GROQ = 197
    DEEPSEEK = 198
    ELEVENLABS = 199
    RUNWAY_ML = 201
    ASSEMBLY_AI = 202
    PINECONE = 203
    WEAVIATE = 204
    CHROMA_DB = 205
    LANGCHAIN = 206
    MOONSHOT_AI = 207
    XAI = 208
    ZHIPU_AI = 209
    AWS = 200
    AZURE = 210
    GCP = 220
    GITHUB = 300
    GITLAB = 310
    BITBUCKET = 320
    STRIPE = 400
    SENDGRID = 410
    TWILIO = 420
    MONGODB = 430
    FIREBASE = 440


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    api_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[ApiStatusEnum] = mapped_column(
        SQLEnum(ApiStatusEnum), default=ApiStatusEnum.UNVERIFIED, index=True
    )
    api_type: Mapped[ApiTypeEnum] = mapped_column(
        SQLEnum(ApiTypeEnum), default=ApiTypeEnum.UNKNOWN, index=True
    )
    search_provider: Mapped[SearchProviderEnum] = mapped_column(SQLEnum(SearchProviderEnum))

    last_checked_utc: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    first_found_utc: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_found_utc: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    times_displayed: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    references: Mapped[List["RepoReference"]] = relationship(back_populates="api_key_obj", cascade="all, delete-orphan")


class RepoReference(Base):
    __tablename__ = "repo_references"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    api_key_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("api_keys.id"), index=True)

    repo_url: Mapped[Optional[str]] = mapped_column(String)
    repo_owner: Mapped[Optional[str]] = mapped_column(String)
    repo_name: Mapped[Optional[str]] = mapped_column(String)
    repo_description: Mapped[Optional[str]] = mapped_column(Text)
    repo_id: Mapped[int] = mapped_column(BigInteger)

    file_url: Mapped[Optional[str]] = mapped_column(String)
    file_name: Mapped[Optional[str]] = mapped_column(String)
    file_path: Mapped[Optional[str]] = mapped_column(String)
    file_sha: Mapped[Optional[str]] = mapped_column(String)
    api_content_url: Mapped[Optional[str]] = mapped_column(String)

    code_context: Mapped[Optional[str]] = mapped_column(Text)
    line_number: Mapped[int] = mapped_column(Integer)

    search_query_id: Mapped[int] = mapped_column(BigInteger)
    found_utc: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    provider: Mapped[Optional[str]] = mapped_column(String)
    branch: Mapped[Optional[str]] = mapped_column(String)

    api_key_obj: Mapped["APIKey"] = relationship(back_populates="references")


class SearchQuery(Base):
    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    search_results_count: Mapped[int] = mapped_column(Integer, default=0)
    last_search_utc: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ApplicationSetting(Base):
    __tablename__ = "application_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String)


class SearchProviderToken(Base):
    __tablename__ = "search_provider_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String, nullable=False)
    search_provider: Mapped[SearchProviderEnum] = mapped_column(
        SQLEnum(SearchProviderEnum), default=SearchProviderEnum.UNKNOWN
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_utc: Mapped[Optional[datetime]] = mapped_column(DateTime)
