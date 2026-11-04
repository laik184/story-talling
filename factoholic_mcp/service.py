from .service_shared import ServiceError
from .service_core import CoreOperations
from .service_queries import QueryOperations
from .service_evaluation import EvaluationOperations
from .service_revision import RevisionOperations

class StorytellingService(CoreOperations, QueryOperations, EvaluationOperations, RevisionOperations):
    pass
