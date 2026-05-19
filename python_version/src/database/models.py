from datetime import datetime, timezone
from enum import Enum
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


class SearchProviderEnum(str, Enum):
    UNKNOWN = "Unknown"
    GITHUB = "GitHub"
    GITLAB = "GitLab"
    BITBUCKET = "BitBucket"
    SOURCEGRAPH = "SourceGraph"


class ApiStatusEnum(str, Enum):
    UNVERIFIED = "Unverified"
    VALID = "Valid"
    INVALID = "Invalid"
    REMOVED = "Removed"
    FLAGGED_FOR_REMOVAL = "FlaggedForRemoval"
    NO_LONGER_WORKING = "NoLongerWorking"
    ERROR = "Error"
    VALID_NO_CREDITS = "ValidNoCredits"


class ApiTypeEnum(str, Enum):
    UNKNOWN = "Unknown"
    OPENAI = "OpenAI"
    AZURE_OPENAI = "AzureOpenAI"
    ANTHROPIC_CLAUDE = "AnthropicClaude"
    GOOGLE_AI = "GoogleAI"
    COHERE = "Cohere"
    HUGGINGFACE = "HuggingFace"
    STABILITY_AI = "StabilityAI"
    MISTRAL_AI = "MistralAI"
    REPLICATE = "Replicate"
    TOGETHER_AI = "TogetherAI"
    OPENROUTER = "OpenRouter"
    PERPLEXITY_AI = "PerplexityAI"
    GROQ = "Groq"
    DEEPSEEK = "DeepSeek"
    ELEVENLABS = "ElevenLabs"
    RUNWAY_ML = "RunwayML"
    ASSEMBLY_AI = "AssemblyAI"
    PINECONE = "Pinecone"
    WEAVIATE = "Weaviate"
    CHROMA_DB = "ChromaDB"
    LANGCHAIN = "LangChain"
    AWS = "AWS"
    AZURE = "Azure"
    GCP = "GCP"
    GITHUB = "GitHub"
    GITLAB = "GitLab"
    BITBUCKET = "BitBucket"
    STRIPE = "Stripe"
    SENDGRID = "SendGrid"
    TWILIO = "Twilio"
    MONGODB = "MongoDB"
    FIREBASE = "Firebase"


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
    first_found_utc: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_found_utc: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

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
