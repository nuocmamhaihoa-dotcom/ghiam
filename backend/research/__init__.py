"""Research engines for the AI Self-Learning Lab."""
from research.patterns import PatternDetector
from research.clusters import ClusterEngine
from research.intents import IntentDiscovery
from research.objections import ObjectionDiscovery
from research.golden import GoldenCallDiscovery
from research.failures import FailurePatternDiscovery
from research.revenue import RevenueLeakDiscovery
from research.coaching import CoachingGenerator

# Aliases for SelfLearningLab import surface
PatternDetector = PatternDetector
IntentDiscovery = IntentDiscovery
ObjectionDiscovery = ObjectionDiscovery
GoldenCallDiscovery = GoldenCallDiscovery
FailurePatternDiscovery = FailurePatternDiscovery
RevenueLeakDiscovery = RevenueLeakDiscovery
ClusterEngine = ClusterEngine

__all__ = [
    "PatternDetector", "ClusterEngine", "IntentDiscovery", "ObjectionDiscovery",
    "GoldenCallDiscovery", "FailurePatternDiscovery", "RevenueLeakDiscovery", "CoachingGenerator",
    "PatternDetector", "IntentDiscovery", "ObjectionDiscovery", "GoldenCallDiscovery",
    "FailurePatternDiscovery", "RevenueLeakDiscovery", "ClusterEngine",
]
