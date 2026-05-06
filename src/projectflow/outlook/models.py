from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutlookAccount:
    id: str
    display_name: str
    email: str = ""
    kind: str = "store"

    @property
    def label(self) -> str:
        if self.email and self.email.lower() not in self.display_name.lower():
            return f"{self.display_name} ({self.email})"
        return self.display_name
