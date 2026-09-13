from dataclasses import dataclass
from abc import ABC, abstractmethod

@dataclass
class DiscoveredDocument:
    source_key: str
    title: str
    official_url: str
    notification_number: str | None = None
    department: str | None = None
    year: int | None = None
    document_type: str = "notification"
    recruitment_key: str | None = None

class SourceAdapter(ABC):
    key: str
    name: str
    listing_url: str

    @abstractmethod
    def discover(self, year: int | None = None, all_years: bool = False) -> list[DiscoveredDocument]:
        raise NotImplementedError
