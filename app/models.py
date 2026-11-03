from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


ProjectFormat = Literal["youtube_16_9", "short_9_16"]
ShotStyle = Literal["quick", "normal", "hold", "demo"]
VerticalStrategy = Literal["crop", "blur", "fit"]
AudioStrategy = Literal["mute", "ambient", "feature"]
AudioMode = Literal["narration", "original", "mixed"]
MasterProfile = Literal["premiere_hq", "premiere_max"]
DeliveryProfile = Literal["youtube_1080p", "social_1080p"]
VoiceQuality = Literal["rapida", "alta", "maxima"]
SyncVerification = Literal["verified", "semantic", "manual", "failed"]
ChronologyMode = Literal["off", "guided", "strict"]
WorkflowMode = Literal["autopilot", "quick_review", "director"]
EditingStyle = Literal["auto", "balanced", "demonstrative", "dynamic"]
VisualObligation = Literal["hard", "contextual", "abstract"]
SyncStrategy = Literal["native", "pause_shaping", "elastic_trim", "micro_tempo", "ai_rewrite"]
RetentionHookType = Literal["pattern_interrupt", "visual_proof", "climax", "standard"]
VisualComplexity = Literal["low", "medium", "high"]


class PhysicalCutPoint(BaseModel):
    timestamp: float = 0.0
    score: float = 0.0
    cut_type: str = "scene_change"


class AudioSilenceWindow(BaseModel):
    start: float = 0.0
    end: float = 0.0
    duration: float = 0.0


class PhysicalMediaMap(BaseModel):
    duration: float = 0.0
    cut_points: list[PhysicalCutPoint] = Field(default_factory=list)
    silence_windows: list[AudioSilenceWindow] = Field(default_factory=list)
    mean_volume_db: float = -20.0
    max_volume_db: float = 0.0
    safe_cut_timestamps: list[float] = Field(default_factory=list)


class VisualAnchor(BaseModel):
    timestamp_sec: float = 0.0
    trigger_phrase: str = ""
    action_label: str = ""
    confidence: float = 1.0


class PhoneticBudget(BaseModel):
    scene_duration_sec: float = 0.0
    effective_speech_sec: float = 0.0
    target_words: int = 0
    min_words: int = 0
    max_words: int = 0
    target_syllables: int = 0
    speech_rate_wps: float = 2.6


class SegmentQuality(str, Enum):
    excellent = "excelente"
    good = "boa"
    usable = "utilizavel"
    poor = "ruim"


class VideoSegment(BaseModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0.001)
    description: str = Field(min_length=3, max_length=1200)
    quality: SegmentQuality = SegmentQuality.good
    tags: list[str] = Field(default_factory=list)
    recommended_use: str = ""
    use: bool = True
    original_audio_is_important: bool = False
    focus_x: float = Field(default=0.5, ge=0, le=1)
    focus_y: float = Field(default=0.5, ge=0, le=1)
    vertical_strategy: VerticalStrategy = "crop"
    shot_style: ShotStyle = "normal"
    min_take_seconds: float = Field(default=1.5, ge=0.3, le=60)
    max_take_seconds: float = Field(default=7.0, ge=0.5, le=120)

    # v3 Evidence Engine. Defaults preserve old project catalogs.
    event_id: str = ""
    event_kind: str = "broll"
    actions: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    visual_phenomena: list[str] = Field(default_factory=list)
    evidence_timestamps: list[float] = Field(default_factory=list)
    confidence: float = Field(default=0.75, ge=0, le=1)
    critical_action: bool = False
    verified_by_user: bool = False
    detection_source: str = "gemini_general"
    visual_anchor: VisualAnchor | None = None
    phonetic_budget: PhoneticBudget | None = None
    sync_precision_score: float = 100.0
    sync_drift_ms: float = 0.0
    applied_sync_strategy: SyncStrategy = "native"
    visual_complexity: VisualComplexity = "medium"
    retention_hook_type: RetentionHookType = "standard"

    @model_validator(mode="after")
    def validate_interval(self) -> "VideoSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds precisa ser maior que start_seconds")
        if self.max_take_seconds < self.min_take_seconds:
            self.max_take_seconds = self.min_take_seconds
        return self


class VideoAnalysis(BaseModel):
    filename: str
    sequence_index: int = Field(default=0, ge=0)
    duration_seconds: float = Field(ge=0.001)
    summary: str = Field(min_length=3, max_length=2600)
    segments: list[VideoSegment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ClipReference(BaseModel):
    filename: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0.001)
    reason: str = ""
    focus_x: float = Field(default=0.5, ge=0, le=1)
    focus_y: float = Field(default=0.5, ge=0, le=1)
    vertical_strategy: VerticalStrategy = "crop"
    shot_style: ShotStyle = "normal"
    min_take_seconds: float = Field(default=1.5, ge=0.3, le=60)
    max_take_seconds: float = Field(default=7.0, ge=0.5, le=120)
    audio_strategy: AudioStrategy = "ambient"

    @model_validator(mode="after")
    def validate_interval(self) -> "ClipReference":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds precisa ser maior que start_seconds")
        if self.max_take_seconds < self.min_take_seconds:
            self.max_take_seconds = self.min_take_seconds
        return self


class ScriptBlock(BaseModel):
    number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=160)
    narration: str = Field(default="", max_length=5000)
    estimated_seconds: float = Field(default=15, ge=0.001, le=600)
    clips: list[ClipReference] = Field(min_length=1)
    editing_note: str = ""
    pacing: Literal["fast", "normal", "slow", "hold"] = "normal"
    block_type: Literal["hook", "body", "demo", "verdict", "cta", "ending"] = "body"
    visual_anchor: Optional[VisualAnchor] = None
    phonetic_budget: Optional[PhoneticBudget] = None
    sync_precision_score: float = 100.0
    sync_drift_ms: float = 0.0
    applied_sync_strategy: SyncStrategy = "native"
    visual_complexity: VisualComplexity = "medium"
    retention_hook_type: RetentionHookType = "standard"

    @field_validator("narration")
    @classmethod
    def normalize_narration(cls, value: str) -> str:
        return " ".join(value.split())

    @property
    def needs_voice(self) -> bool:
        return bool(self.narration.strip())


class EditPlan(BaseModel):
    project_title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1000)
    target_duration_seconds: float = Field(default=480, ge=0.001, le=7200)
    blocks: list[ScriptBlock] = Field(min_length=1)
    general_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def renumber_blocks(self) -> "EditPlan":
        for index, block in enumerate(self.blocks, start=1):
            block.number = index
        return self


class NarrationBeat(BaseModel):
    beat_id: str
    block_number: int = Field(ge=1)
    beat_number: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=1200)
    output_start_seconds: float = Field(ge=0)
    output_end_seconds: float = Field(ge=0.001)
    required_observation: str = ""
    critical: bool = False
    visual_obligation: VisualObligation = "contextual"
    timing_source: Literal["audio_aligned", "estimated"] = "estimated"
    timing_confidence: float = Field(default=0.5, ge=0, le=1)
    visual_anchor: VisualAnchor | None = None
    phonetic_budget: PhoneticBudget | None = None
    sync_precision_score: float = 100.0
    sync_drift_ms: float = 0.0
    applied_sync_strategy: SyncStrategy = "native"
    visual_complexity: VisualComplexity = "medium"
    retention_hook_type: RetentionHookType = "standard"

    @model_validator(mode="after")
    def validate_interval(self) -> "NarrationBeat":
        if self.output_end_seconds <= self.output_start_seconds:
            raise ValueError("output_end_seconds precisa ser maior que output_start_seconds")
        return self


class AudioBeatTiming(BaseModel):
    beat_id: str
    speech_start_seconds: float = Field(ge=0)
    speech_end_seconds: float = Field(ge=0.001)
    confidence: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def validate_interval(self) -> "AudioBeatTiming":
        if self.speech_end_seconds <= self.speech_start_seconds:
            raise ValueError("speech_end_seconds precisa ser maior que speech_start_seconds")
        return self


class AudioAlignmentPlan(BaseModel):
    timings: list[AudioBeatTiming] = Field(default_factory=list)


class SyncCandidate(BaseModel):
    event_id: str
    score: float = Field(default=0.5, ge=0, le=1)
    reason: str = ""


class SyncMatch(BaseModel):
    beat_id: str
    required_observation: str = ""
    critical: bool = False
    candidates: list[SyncCandidate] = Field(default_factory=list)


class SyncMatchPlan(BaseModel):
    matches: list[SyncMatch] = Field(default_factory=list)


class VisualVerification(BaseModel):
    confirmed: bool = False
    confidence: float = Field(default=0, ge=0, le=1)
    observed_evidence: str = ""
    contradiction: str = ""
    recommended_start_seconds: float | None = Field(default=None, ge=0)
    recommended_end_seconds: float | None = Field(default=None, ge=0)


class VisualRun(BaseModel):
    """One actual edit decision. Semantic beats may share one long visual run."""
    run_id: str
    block_number: int = Field(ge=1)
    output_start_seconds: float = Field(ge=0)
    output_end_seconds: float = Field(ge=0.001)
    filename: str = ""
    source_start_seconds: float = Field(default=0, ge=0)
    source_end_seconds: float = Field(default=0, ge=0)
    beat_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    evidence_description: str = ""
    reason: str = ""
    verification: SyncVerification = "semantic"
    confidence: float = Field(default=0, ge=0, le=1)
    vertical_strategy: VerticalStrategy = "crop"
    focus_x: float = Field(default=0.5, ge=0, le=1)
    focus_y: float = Field(default=0.5, ge=0, le=1)
    audio_strategy: AudioStrategy = "ambient"
    shot_style: ShotStyle = "normal"
    sequence_index: int = Field(default=0, ge=0)
    allows_replay: bool = False
    visual_anchor: VisualAnchor | None = None
    phonetic_budget: PhoneticBudget | None = None
    sync_precision_score: float = 100.0
    sync_drift_ms: float = 0.0
    applied_sync_strategy: SyncStrategy = "native"
    visual_complexity: VisualComplexity = "medium"
    retention_hook_type: RetentionHookType = "standard"

    @model_validator(mode="after")
    def validate_interval(self) -> "VisualRun":
        if self.output_end_seconds <= self.output_start_seconds:
            raise ValueError("output_end_seconds precisa ser maior que output_start_seconds")
        if self.source_end_seconds <= self.source_start_seconds:
            raise ValueError("source_end_seconds precisa ser maior que source_start_seconds")
        return self

    @property
    def duration(self) -> float:
        return max(0.0, self.output_end_seconds - self.output_start_seconds)


class EditQualityReport(BaseModel):
    passed: bool = True
    editing_style: EditingStyle = "auto"
    run_count: int = Field(default=0, ge=0)
    cut_count: int = Field(default=0, ge=0)
    cuts_per_minute: float = Field(default=0, ge=0)
    short_run_count: int = Field(default=0, ge=0)
    source_reset_count: int = Field(default=0, ge=0)
    repeated_overlap_count: int = Field(default=0, ge=0)
    ping_pong_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list)


class ProfessionalSyncBeat(BaseModel):
    beat_id: str
    block_number: int
    beat_number: int
    text: str
    output_start_seconds: float
    output_end_seconds: float
    required_observation: str = ""
    critical: bool = False
    visual_obligation: VisualObligation = "contextual"
    timing_source: Literal["audio_aligned", "estimated"] = "estimated"
    timing_confidence: float = Field(default=0.5, ge=0, le=1)
    visual_anchor: VisualAnchor | None = None
    phonetic_budget: PhoneticBudget | None = None
    sync_precision_score: float = 100.0
    sync_drift_ms: float = 0.0
    applied_sync_strategy: SyncStrategy = "native"
    visual_complexity: VisualComplexity = "medium"
    retention_hook_type: RetentionHookType = "standard"
    event_id: str = ""
    filename: str = ""
    source_start_seconds: float = 0
    source_end_seconds: float = 0
    evidence_description: str = ""
    match_reason: str = ""
    match_confidence: float = Field(default=0, ge=0, le=1)
    verification: SyncVerification = "failed"
    vertical_strategy: VerticalStrategy = "crop"
    focus_x: float = Field(default=0.5, ge=0, le=1)
    focus_y: float = Field(default=0.5, ge=0, le=1)
    audio_strategy: AudioStrategy = "ambient"
    shot_style: ShotStyle = "normal"
    sequence_index: int = Field(default=0, ge=0)

    @property
    def duration(self) -> float:
        return max(0.0, self.output_end_seconds - self.output_start_seconds)


class ProfessionalSyncPlan(BaseModel):
    version: str = "6.0"
    total_duration_seconds: float = Field(ge=0)
    generated_at: str = ""
    beats: list[ProfessionalSyncBeat] = Field(default_factory=list)
    visual_runs: list[VisualRun] = Field(default_factory=list)
    edit_quality: EditQualityReport | None = None
    warnings: list[str] = Field(default_factory=list)
    ready: bool = False
    evidence_mode: str = "high_fps_verified"
    timing_mode: str = "audio_aligned"
    chronology_mode: ChronologyMode = "guided"
    rough_cut_qa: RoughCutReview | None = None


class ProjectKnowledge(BaseModel):
    workflow_mode: WorkflowMode = "autopilot"
    editing_style: EditingStyle = "auto"
    chronology_source: Literal["auto", "manual"] = "auto"
    locked_facts: list[str] = Field(default_factory=list)
    chronology_mode: ChronologyMode = "guided"
    footage_order: list[str] = Field(default_factory=list)
    chronology_notes: str = Field(default="", max_length=4000)
    updated_at: str = ""

    @field_validator("locked_facts")
    @classmethod
    def normalize_facts(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = " ".join(str(value).split()).strip()
            if item and item.casefold() not in seen:
                cleaned.append(item)
                seen.add(item.casefold())
        return cleaned[:200]


class ChronologyInference(BaseModel):
    ordered_filenames: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    rationale: str = Field(default="", max_length=2000)


class DirectedEvidenceSearchRequest(BaseModel):
    target: str = Field(min_length=3, max_length=800)
    filenames: list[str] = Field(default_factory=list)
    max_candidates: int = Field(default=6, ge=1, le=12)


class DirectedEvidenceCandidate(BaseModel):
    filename: str
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(ge=0.001)
    observed_evidence: str = ""
    confidence: float = Field(default=0, ge=0, le=1)
    confirmed: bool = False
    model: str = ""
    detection_source: Literal["gemini_slow_scan", "local_banding", "manual"] = "gemini_slow_scan"
    visual_phenomena: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_window(self) -> "DirectedEvidenceCandidate":
        if self.source_end_seconds <= self.source_start_seconds:
            raise ValueError("source_end_seconds precisa ser maior que source_start_seconds")
        return self


class DirectedEvidenceSearchResult(BaseModel):
    target: str
    created_at: str = ""
    candidates: list[DirectedEvidenceCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ManualEvidenceRequest(BaseModel):
    filename: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0.001)
    label: str = Field(min_length=3, max_length=500)
    event_kind: str = "phenomenon"
    actions: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    visual_phenomena: list[str] = Field(default_factory=list)
    critical_action: bool = True

    @model_validator(mode="after")
    def validate_interval(self) -> "ManualEvidenceRequest":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds precisa ser maior que start_seconds")
        return self


class DirectedClipFinding(BaseModel):
    confirmed: bool = False
    confidence: float = Field(default=0, ge=0, le=1)
    observed_evidence: str = ""
    contradiction: str = ""
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    visual_phenomena: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)


class RoughCutIssue(BaseModel):
    severity: Literal["info", "warning", "critical"] = "warning"
    beat_id: str = ""
    output_start_seconds: float = Field(default=0, ge=0)
    output_end_seconds: float = Field(default=0, ge=0)
    issue_type: Literal["wrong_scene", "wrong_state", "missing_action", "missing_phenomenon", "timing", "chronology", "framing", "other"] = "other"
    description: str = ""
    suggested_fix: str = ""


class RoughCutReview(BaseModel):
    approved: bool = False
    confidence: float = Field(default=0, ge=0, le=1)
    summary: str = ""
    issues: list[RoughCutIssue] = Field(default_factory=list)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    # Backward-compatible internal field: may contain a product, event, person, place or general theme.
    # It is optional because many videos (vlogs, kids content, tutorials, gameplay, etc.) have no product.
    product: str = Field(default="", max_length=200)
    objective: str = Field(default="Criar um video claro, envolvente e pronto para publicacao", max_length=1000)
    format: ProjectFormat = "youtube_16_9"
    desired_minutes: int = Field(default=10, ge=1, le=90)
    short_seconds: int = Field(default=60, ge=30, le=120)
    audio_mode: AudioMode = "narration"
    create_master: bool = True
    master_profile: MasterProfile = "premiere_hq"
    create_delivery: bool = True
    delivery_profile: DeliveryProfile = "youtube_1080p"

    @model_validator(mode="after")
    def validate_format_duration(self) -> "ProjectCreate":
        if self.format == "short_9_16" and not 30 <= self.short_seconds <= 120:
            raise ValueError("Shorts precisam ter entre 30 e 120 segundos")
        if not self.create_master and not self.create_delivery:
            raise ValueError("Selecione pelo menos uma saída de vídeo: Master ou arquivo de publicação")
        return self


class ProjectSettings(ProjectCreate):
    slug: str
    created_at: str
    model: str
    resolution: Literal["1920x1080", "1080x1920"]
    fps: int = Field(default=30, ge=24, le=60)

    @property
    def target_duration_seconds(self) -> int:
        return self.short_seconds if self.format == "short_9_16" else self.desired_minutes * 60


class ProjectSummary(BaseModel):
    slug: str
    name: str
    product: str = ""
    created_at: str
    status: str
    root: str
    format: ProjectFormat = "youtube_16_9"


class JobState(BaseModel):
    project_slug: str
    job: Literal["analysis", "script", "voice", "evidence_search", "sync", "assembly", "publication", "autopilot"]
    status: Literal["idle", "queued", "running", "completed", "failed"] = "idle"
    progress: int = Field(default=0, ge=0, le=100)
    message: str = ""
    error: str | None = None
    updated_at: str = ""
    block_number: int | None = None


class PublicationContext(BaseModel):
    """Generic editorial context inferred before web research/publication.

    The app must work for products, tutorials, vlogs, kids/family content, events,
    travel, gaming, recipes, education and arbitrary themes. Product-specific
    research is only enabled when the content is actually about a product.
    """
    content_type: Literal[
        "product_review", "tutorial", "vlog_event", "kids_family", "travel_place",
        "gaming", "recipe_food", "education_explainer", "comparison",
        "news_commentary", "entertainment", "other"
    ] = "other"
    primary_subject: str = ""
    product_name: str = ""
    is_product_specific: bool = False
    factual_research_relevant: bool = False
    research_mode: Literal["product_official", "subject_official", "none"] = "none"
    research_query: str = ""
    technical_specs_relevant: bool = False
    audience: str = "geral"
    search_intents: list[str] = Field(default_factory=list)
    description_focus: list[str] = Field(default_factory=list)


class PublicationSpecFact(BaseModel):
    label: str = ""
    value: str = ""


class PublicationFactSheet(BaseModel):
    exact_subject_match: bool = False
    subject: str = ""
    variant_notes: str = ""
    specifications: list[PublicationSpecFact] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    package_contents: list[str] = Field(default_factory=list)


class PublicationSource(BaseModel):
    title: str = ""
    url: str = ""


class PublicationDescriptionSection(BaseModel):
    title: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class PublicationDescriptionBlueprint(BaseModel):
    opening_paragraphs: list[str] = Field(default_factory=list)
    sections: list[PublicationDescriptionSection] = Field(default_factory=list)
    closing_paragraphs: list[str] = Field(default_factory=list)


class PublicationPackage(BaseModel):
    version: str = "6.0.0"
    content_type: str = "other"
    research_mode: str = "none"
    recommended_title: str = Field(min_length=3, max_length=100)
    alternative_titles: list[str] = Field(default_factory=list)
    description: str = Field(min_length=20, max_length=5000)
    pinned_comment: str = Field(min_length=5, max_length=1200)
    tags: list[str] = Field(default_factory=list)
    thumbnail_idea: str = Field(default="", max_length=1200)
    research_status: str = "not_run"
    research_sources: list[PublicationSource] = Field(default_factory=list)


class VoiceProfileSettings(BaseModel):
    profile_id: str = "sandro"
    display_name: str = "Sandro / Dr. Saygon"
    reference_original: str = ""
    reference_clean: str = ""
    embedding_path: str = ""
    prepared: bool = False
    pronunciation_rules: dict[str, str] = Field(default_factory=dict)
    updated_at: str = ""


class VoiceGenerateRequest(BaseModel):
    candidates: int = Field(default=2, ge=1, le=3)
    quality: VoiceQuality = "alta"


class ProjectPaths(BaseModel):
    root: Path
    footage: Path
    information: Path
    analysis: Path
    narration: Path
    result: Path
    publication: Path
    temp: Path
    logs: Path
