"""Pydantic schema for Layer 3: Knowledge (semantic engineering knowledge).

This is the extraction output from the LLM, containing entities, relationships,
claims, procedures, safety items, and references. Every item requires evidence
traceable to the source document.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from knowledge_layer.schemas.claims import EngineeringClaim


class EntityDomain(str, Enum):
    """Top-level domains for entity classification."""
    PLANT_STRUCTURE = "plant_structure"
    PROCESS = "process"
    EQUIPMENT = "equipment"
    PIPING = "piping"
    VALVE = "valve"
    INSTRUMENTATION = "instrumentation"
    CONTROL = "control"
    OPERATIONS = "operations"
    SAFETY = "safety"
    MAINTENANCE = "maintenance"
    CHEMICAL = "chemical"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


class EntityType(str, Enum):
    """Specific entity types within domains."""
    # Plant structure
    REFINERY = "refinery"
    PLANT = "plant"
    BLOCK = "block"
    UNIT = "unit"
    SECTION = "section"
    AREA = "area"
    BATTERY_LIMIT = "battery_limit"
    OFFSITE = "offsite"

    # Process
    PROCESS = "process"
    FEED = "feed"
    STREAM = "stream"
    PRODUCT = "product"
    INTERMEDIATE = "intermediate"
    RECYCLE = "recycle"
    EFFLUENT = "effluent"
    FUEL = "fuel"
    UTILITY = "utility"

    # Equipment
    PUMP = "pump"
    COMPRESSOR = "compressor"
    MOTOR = "motor"
    TURBINE = "turbine"
    FURNACE = "furnace"
    HEATER = "heater"
    COLUMN = "column"
    TRAY = "tray"
    REACTOR = "reactor"
    VESSEL = "vessel"
    DRUM = "drum"
    TANK = "tank"
    HEAT_EXCHANGER = "heat_exchanger"
    CONDENSER = "condenser"
    REBOILER = "reboiler"
    DESALTER = "desalter"
    EJECTOR = "ejector"
    VACUUM_SYSTEM = "vacuum_system"
    FILTER = "filter"

    # Piping
    PIPELINE = "pipeline"
    PROCESS_LINE = "process_line"
    HEADER = "header"
    MANIFOLD = "manifold"
    BRANCH = "branch"
    NOZZLE = "nozzle"

    # Valves
    VALVE = "valve"
    ISOLATION_VALVE = "isolation_valve"
    CONTROL_VALVE = "control_valve"
    CHECK_VALVE = "check_valve"
    SAFETY_VALVE = "safety_valve"
    RELIEF_VALVE = "relief_valve"
    ACTUATED_VALVE = "actuated_valve"

    # Instrumentation
    INSTRUMENT = "instrument"
    TRANSMITTER = "transmitter"
    INDICATOR = "indicator"
    CONTROLLER = "controller"
    ANALYZER = "analyzer"
    PRESSURE_INSTRUMENT = "pressure_instrument"
    TEMPERATURE_INSTRUMENT = "temperature_instrument"
    FLOW_INSTRUMENT = "flow_instrument"
    LEVEL_INSTRUMENT = "level_instrument"
    SWITCH = "switch"
    ALARM = "alarm"
    TRIP = "trip"
    INTERLOCK = "interlock"

    # Control
    CONTROL_LOOP = "control_loop"
    DCS = "dcs"
    PLC = "plc"
    APC = "apc"
    CONTROL_SCHEME = "control_scheme"

    # Operations
    OPERATING_CONDITION = "operating_condition"
    OPERATING_LIMIT = "operating_limit"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    EMERGENCY_SHUTDOWN = "emergency_shutdown"
    PROCEDURE = "procedure"
    CHECKLIST = "checklist"
    SAMPLING = "sampling"

    # Safety
    HAZARD = "hazard"
    SAFEGUARD = "safeguard"
    RELIEF_SYSTEM = "relief_system"
    FIRE_PROTECTION = "fire_protection"
    GAS_DETECTION = "gas_detection"
    LEL_DETECTOR = "lel_detector"
    PPE = "ppe"
    PERMIT = "permit"
    ISOLATION = "isolation"
    LOCKOUT_TAGOUT = "lockout_tagout"
    CONFINED_SPACE = "confined_space"

    # Maintenance
    MAINTENANCE_ACTIVITY = "maintenance_activity"
    INSPECTION = "inspection"
    TEST = "test"
    TURNAROUND = "turnaround"
    CORROSION_MONITORING = "corrosion_monitoring"
    CORROSION_PROBE = "corrosion_probe"
    COUPON = "coupon"

    # Chemical
    CHEMICAL = "chemical"
    TREATMENT_CHEMICAL = "treatment_chemical"
    HAZARDOUS_CHEMICAL = "hazardous_chemical"
    REAGENT = "reagent"

    # Document
    DOCUMENT_REF = "document_ref"
    DRAWING_REF = "drawing_ref"

    # Generic
    GENERIC = "generic"
    OTHER = "other"


class RelationshipType(str, Enum):
    """Engineering relationship types between entities."""
    # Structural
    PART_OF = "PART_OF"
    LOCATED_IN = "LOCATED_IN"

    # Process flow
    UPSTREAM_OF = "UPSTREAM_OF"
    DOWNSTREAM_OF = "DOWNSTREAM_OF"
    CONNECTED_TO = "CONNECTED_TO"
    FEEDS = "FEEDS"
    RECEIVES_FROM = "RECEIVES_FROM"
    DISCHARGES_TO = "DISCHARGES_TO"
    ROUTES_TO = "ROUTES_TO"
    SUCTION_FROM = "SUCTION_FROM"
    RETURN_TO = "RETURN_TO"
    RECYCLES_TO = "RECYCLES_TO"

    # Mechanical
    DRIVEN_BY = "DRIVEN_BY"
    HEATED_BY = "HEATED_BY"
    COOLED_BY = "COOLED_BY"
    CONDENSED_BY = "CONDENSED_BY"
    SEPARATED_BY = "SEPARATED_BY"
    DISTILLED_BY = "DISTILLED_BY"
    STRIPPED_BY = "STRIPPED_BY"

    # Instrumentation and control
    CONTROLLED_BY = "CONTROLLED_BY"
    MEASURED_BY = "MEASURED_BY"
    MONITORED_BY = "MONITORED_BY"
    INDICATED_BY = "INDICATED_BY"
    ALARMED_BY = "ALARMED_BY"
    TRIPPED_BY = "TRIPPED_BY"
    INTERLOCKED_WITH = "INTERLOCKED_WITH"

    # Safety
    PROTECTED_BY = "PROTECTED_BY"
    RELIEVED_BY = "RELIEVED_BY"

    # Operations
    OPERATES_IN = "OPERATES_IN"
    HAS_FEED = "HAS_FEED"
    HAS_PRODUCT = "HAS_PRODUCT"
    USES_UTILITY = "USES_UTILITY"
    RECEIVES_UTILITY = "RECEIVES_UTILITY"
    SUPPLIES = "SUPPLIES"

    # General
    APPLIES_TO = "APPLIES_TO"
    REQUIRES = "REQUIRES"
    REFERENCES = "REFERENCES"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"


class ExtractedEntity(BaseModel):
    """An entity extracted from the source document."""
    entity_id: str = Field(description="Temporary ID for this extraction batch")
    name: str = Field(description="Entity name as it appears in the source")
    canonical_name: str = Field(default="", description="Normalized/canonical name")
    entity_type: EntityType
    entity_type_raw: str = Field(default="", description="Type label exactly as the model produced it")
    domain: EntityDomain = EntityDomain.UNKNOWN
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(default="")
    canonical_tag: str | None = Field(default=None, description="Canonical asset tag (global identity) if any")
    uid: str = Field(default="", description="Graph UID assigned by the resolver")
    resolution_method: str = Field(default="", description="tag | glossary | name_match | vector | document_scoped")
    grounding: str = Field(default="strong", description="strong = evidence sentence found in chunk; weak = name only")
    source: str = Field(default="llm_pass1", description="llm_pass1 | llm_pass2 | rule | table")
    sentence_index: int | None = Field(default=None)

    # Provenance
    evidence: str = Field(description="Source text where this entity was identified")
    page: int
    section: str = Field(default="")
    document_id: str = Field(default="")
    chunk_id: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractedRelationship(BaseModel):
    """A relationship between two entities, with required evidence."""
    relationship_id: str
    subject: str = Field(description="Subject entity name or ID")
    subject_type: EntityType | None = None
    predicate: RelationshipType
    predicate_raw: str = Field(default="", description="Predicate label exactly as the model produced it")
    object: str = Field(description="Object entity name or ID")
    object_type: EntityType | None = None
    grounding: str = Field(default="strong", description="strong = one sentence names subject and object; weak = both appear but apart")
    source: str = Field(default="llm_pass1", description="llm_pass1 | llm_pass2 | rule | table")
    sentence_index: int | None = Field(default=None)

    # Provenance (REQUIRED — no orphan relationships)
    evidence: str = Field(description="Source text explicitly establishing this relationship")
    page: int
    section: str = Field(default="")
    document_id: str = Field(default="")
    chunk_id: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractedProcedure(BaseModel):
    """A procedure extracted from the document."""
    procedure_id: str
    title: str
    procedure_type: str = Field(
        default="",
        description="e.g., 'startup', 'shutdown', 'emergency', 'maintenance', 'sampling'",
    )
    steps: list[ProcedureStep] = Field(default_factory=list)
    applies_to: list[str] = Field(default_factory=list, description="Entity names this applies to")
    preconditions: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)

    # Provenance
    evidence: str = Field(default="")
    page: int = 0
    section: str = Field(default="")
    document_id: str = Field(default="")
    chunk_id: str = Field(default="")


class ProcedureStep(BaseModel):
    """A single step in a procedure, in original order."""
    sequence: int = Field(description="Step number in original order")
    instruction: str
    conditions: list[str] = Field(default_factory=list, description="Conditions for this step")
    safety_notes: list[str] = Field(default_factory=list)
    equipment_involved: list[str] = Field(default_factory=list)
    page: int = 0


# Fix forward reference
ExtractedProcedure.model_rebuild()


class SafetyItem(BaseModel):
    """A safety-related item extracted from the document."""
    item_id: str
    category: str = Field(description="e.g., 'hazard', 'safeguard', 'ppe', 'permit', 'interlock'")
    description: str
    severity: str = Field(default="", description="If specified in document")
    applies_to: list[str] = Field(default_factory=list)
    mitigation: str = Field(default="")

    # Provenance
    evidence: str
    page: int
    section: str = Field(default="")
    document_id: str = Field(default="")
    chunk_id: str = Field(default="")


class ExtractionUncertainty(BaseModel):
    """Something the model couldn't confidently extract — flagged for review."""
    description: str
    reason: str = Field(description="Why the model is uncertain")
    source_text: str = Field(default="")
    page: int = 0
    chunk_id: str = Field(default="")


class ChunkExtraction(BaseModel):
    """Complete extraction output for a single chunk."""
    chunk_id: str
    document_id: str
    page_start: int
    page_end: int
    section: str = Field(default="")

    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    claims: list[EngineeringClaim] = Field(default_factory=list)
    procedures: list[ExtractedProcedure] = Field(default_factory=list)
    safety_items: list[SafetyItem] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list, description="Document references found")
    uncertainties: list[ExtractionUncertainty] = Field(default_factory=list)

    # Extraction metadata
    model_name: str = Field(default="deepseek-r1:7b")
    extraction_timestamp: str = Field(default="")
    extraction_duration_seconds: float = Field(default=0.0)
    was_cross_checked: bool = Field(default=False)


class DocumentKnowledge(BaseModel):
    """Layer 3: Complete semantic engineering knowledge for a document.

    This is the aggregation of all chunk extractions after validation,
    representing the full engineering knowledge extracted from one document.
    """
    document_id: str
    source_filename: str

    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    claims: list[EngineeringClaim] = Field(default_factory=list)
    procedures: list[ExtractedProcedure] = Field(default_factory=list)
    safety_items: list[SafetyItem] = Field(default_factory=list)
    uncertainties: list[ExtractionUncertainty] = Field(default_factory=list)

    # Cross-document references
    referenced_documents: list[str] = Field(default_factory=list)
    missing_documents: list[str] = Field(default_factory=list)

    # Quality summary
    total_chunks_processed: int = 0
    total_chunks_cross_checked: int = 0
    validation_failures: int = 0
    contradictions_found: int = 0

    knowledge_timestamp: str = Field(default="")
