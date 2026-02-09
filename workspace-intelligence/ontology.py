"""
Workspace Intelligence Layer - Core Ontology

Defines the semantic graph schema for representing codebases.
"""

from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime, timezone


# =============================================================================
# NODE TYPES
# =============================================================================

class NodeType(str, Enum):
    """Classification of nodes in the workspace graph."""
    
    # Workspace Level
    WORKSPACE = "Workspace"       # Root container
    PROJECT = "Project"           # Repo/app/package inside workspace
    
    # Macro / Infrastructure
    SERVICE = "Service"           # Deployable unit (API, Worker, Frontend)
    RESOURCE = "Resource"         # Infra: Database, Redis, S3, Queue
    EXTERNAL_API = "ExternalAPI"  # Third-party dependency (Stripe, Twilio)
    INFRA_CONFIG = "InfraConfig"  # Docker, k8s, Terraform, .env
    
    # Micro / Code
    FILE = "File"                 # Physical source file
    MODULE = "Module"             # Logical grouping (folder/package)
    ENDPOINT = "Endpoint"         # HTTP route handler
    ASYNC_HANDLER = "AsyncHandler"  # Event consumer, background job, cron
    FUNCTION = "Function"         # Architecturally significant business logic
    DATA_MODEL = "DataModel"      # DB schema / ORM entity
    EVENT = "Event"               # Named business event (ORDER_CREATED)
    CACHE_KEY = "CacheKey"        # Named cache entry pattern


# =============================================================================
# EDGE TYPES
# =============================================================================

class EdgeType(str, Enum):
    """Classification of relationships between nodes."""
    
    # Structural
    CONTAINS = "CONTAINS"         # Service -> Endpoint, Project -> Service
    DEFINES = "DEFINES"           # File -> DataModel
    IMPORTS = "IMPORTS"           # File -> File
    
    # Operational (The "Story")
    READS_DB = "READS_DB"         # Endpoint -> DataModel
    WRITES_DB = "WRITES_DB"       # Endpoint -> DataModel
    CALLS_API = "CALLS_API"       # Function -> ExternalAPI
    CALLS_SERVICE = "CALLS_SERVICE"  # Service -> Service (inter-service)
    EMITS_EVENT = "EMITS_EVENT"   # Service/Function -> Event
    CONSUMES_EVENT = "CONSUMES_EVENT"  # AsyncHandler -> Event
    CACHE_READ = "CACHE_READ"     # Function -> CacheKey
    CACHE_WRITE = "CACHE_WRITE"   # Function -> CacheKey
    WEBHOOK_SEND = "WEBHOOK_SEND"     # Service -> ExternalAPI
    WEBHOOK_RECEIVE = "WEBHOOK_RECEIVE"  # Endpoint -> ExternalAPI
    
    # Deployment
    DEPLOYED_BY = "DEPLOYED_BY"   # Service -> InfraConfig
    DEPENDS_ON = "DEPENDS_ON"     # Service -> Resource


# =============================================================================
# SOURCE LOCATION
# =============================================================================

class SourceLocation(BaseModel):
    """Points to a specific location in source code."""
    file_path: str
    start_line: int
    end_line: int


# =============================================================================
# GRAPH NODE
# =============================================================================

class GraphNode(BaseModel):
    """
    A node in the workspace intelligence graph.
    
    Represents any semantic entity: Service, Endpoint, DataModel, Event, etc.
    """
    id: str = Field(
        ..., 
        description="Unique identifier. Format: '{type}:{namespace}:{name}' e.g., 'endpoint:user-api:POST:/users'"
    )
    type: NodeType
    name: str = Field(..., description="Human readable name")
    description: Optional[str] = Field(
        None, 
        description="Agent-generated summary of responsibility/purpose"
    )
    location: Optional[SourceLocation] = None
    
    # Confidence & Provenance
    confidence: float = Field(
        default=1.0, 
        ge=0.0, 
        le=1.0,
        description="Agent's confidence in this node's classification (0.0-1.0)"
    )
    is_stale: bool = Field(
        default=False,
        description="Marked for re-indexing after source change"
    )
    
    # Metadata
    metadata: Dict[str, Any] = Field(
        default_factory=dict, 
        description="Arbitrary metadata (tags, framework, etc.)"
    )
    last_updated: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# =============================================================================
# GRAPH EDGE
# =============================================================================

class GraphEdge(BaseModel):
    """
    A directed edge in the workspace intelligence graph.
    
    Represents a relationship between two nodes.
    """
    source_id: str
    target_id: str
    type: EdgeType
    description: Optional[str] = Field(
        None, 
        description="Context about the relationship (e.g., 'writes to users table on signup')"
    )
    
    # Confidence & Provenance
    confidence: float = Field(
        default=1.0, 
        ge=0.0, 
        le=1.0,
        description="Agent's confidence in this relationship (0.0-1.0)"
    )
    
    metadata: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# CONTEXT PACK (Skill API Output)
# =============================================================================

class ContextPack(BaseModel):
    """
    The output of the Skill API.
    
    Provides architectural context for AI agents working on a specific task.
    """
    scope: str = Field(..., description="The queried scope (e.g., 'Service: OrderService')")
    focus: str = Field(..., description="The task focus (e.g., 'Refactoring database schema')")
    
    relevant_nodes: List[GraphNode] = Field(default_factory=list)
    upstream: List[GraphNode] = Field(
        default_factory=list,
        description="Nodes that depend on / call into the scope"
    )
    downstream: List[GraphNode] = Field(
        default_factory=list,
        description="Nodes that the scope calls / triggers"
    )
    
    risk_assessment: Optional[str] = Field(
        None,
        description="Agent-generated risk summary (e.g., 'High Risk: This table is read by Analytics')"
    )
