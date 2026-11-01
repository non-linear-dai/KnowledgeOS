"""Optional assistant gateway for KnowledgeOS authoring.

This package is deliberately separate from ``knowledge_os``: it is the only
component that talks to a language model provider. It reaches KnowledgeOS
only through the public HTTP API with the requesting user's own token, so
every context read, dry run and later proposal is authorized as that user.
It never writes: drafts come back to Studio, where a person adopts them and
submits through the normal governed proposal endpoints.
"""

__version__ = "1.0.0"
