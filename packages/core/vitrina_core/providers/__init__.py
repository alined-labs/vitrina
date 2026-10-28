"""Provider interfaces (plan.md §4.5, T010): every external model or messaging call goes
through one of these protocols, so replay mode can swap all providers at once (AS-7.3).

Adapters live in ``providers/*``; ``Recorded*`` wrappers replay JSON cassettes.
"""

from vitrina_core.providers.audio import Transcriber, TranscriptionRequest, TranscriptionResponse
from vitrina_core.providers.base import Blob, CallMeta, ModelPurpose, ProviderName
from vitrina_core.providers.chat import (
    ChatMessage,
    ChatModel,
    ChatRequest,
    ChatResponse,
    ContentPart,
    ImagePart,
    ResponseFormat,
    TextPart,
    ToolCall,
    ToolSpec,
)
from vitrina_core.providers.embeddings import Embedder, EmbeddingRequest, EmbeddingResponse
from vitrina_core.providers.errors import (
    CassetteMissError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
)
from vitrina_core.providers.images import (
    Box,
    ImageModel,
    ImageRequest,
    ImageResponse,
    Segment,
    Segmenter,
    SegmentRequest,
    SegmentResponse,
)
from vitrina_core.providers.messaging import (
    Messenger,
    OutboundImage,
    OutboundMessage,
    OutboundTemplate,
    OutboundText,
    SendResult,
)
from vitrina_core.providers.recorded import (
    CassetteMode,
    Cassettes,
    RecordedChatModel,
    RecordedEmbedder,
    RecordedImageModel,
    RecordedSegmenter,
    RecordedTranscriber,
)
from vitrina_core.providers.retry import is_retryable, with_backoff
from vitrina_core.settings import ReasoningEffort

__all__ = [
    "Blob",
    "Box",
    "CallMeta",
    "CassetteMissError",
    "CassetteMode",
    "Cassettes",
    "ChatMessage",
    "ChatModel",
    "ChatRequest",
    "ChatResponse",
    "ContentPart",
    "Embedder",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "ImageModel",
    "ImagePart",
    "ImageRequest",
    "ImageResponse",
    "Messenger",
    "ModelPurpose",
    "OutboundImage",
    "OutboundMessage",
    "OutboundTemplate",
    "OutboundText",
    "ProviderError",
    "ProviderName",
    "ProviderUnavailableError",
    "RateLimitedError",
    "ReasoningEffort",
    "RecordedChatModel",
    "RecordedEmbedder",
    "RecordedImageModel",
    "RecordedSegmenter",
    "RecordedTranscriber",
    "ResponseFormat",
    "Segment",
    "SegmentRequest",
    "SegmentResponse",
    "Segmenter",
    "SendResult",
    "TextPart",
    "ToolCall",
    "ToolSpec",
    "Transcriber",
    "TranscriptionRequest",
    "TranscriptionResponse",
    "is_retryable",
    "with_backoff",
]
