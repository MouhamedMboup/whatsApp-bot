import hashlib
from dataclasses import dataclass
from typing import List, Optional, Tuple

from django.conf import settings
from django.db.models import Count, Max, Q
from django.utils import timezone

from ..models import DriverProfile, DriverSignal
from .identity_service import IdentityService


@dataclass(frozen=True)
class TrustReputationSummary:
    driver_name: str
    masked_permit: str
    status_emoji: str
    status_label: str
    distinct_owners: int
    top_reasons: List[str]
    last_signal_relative: str
    details: Tuple[int, int, int]  # (grave, serious, positive)


class TrustReputationService:
    """
    Trust-focused reputation aggregation (Yango owner context).
    Permit is the primary identifier (fingerprinted).
    """

    STATUS_GREEN = ("🟢", "FIABLE")
    STATUS_ORANGE = ("🟠", "À SURVEILLER")
    STATUS_RED = ("🔴", "DÉCONSEILLÉ")

    @staticmethod
    def fingerprint_permit(permit_normalized: str) -> str:
        """
        Deterministic fingerprint for permit-based lookups.
        Avoids storing permit in clear while enabling indexed queries.
        """
        raw = f"permit:{permit_normalized}:{settings.SECRET_KEY}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def mask_phone_e164(phone: str) -> str:
        if not phone or not phone.startswith("+") or len(phone) < 8:
            return "****"
        # Keep country code + first 1-2 digits after it, and last 2-3 digits
        # Example: +22501234567 -> +2250*****67
        keep_prefix = min(5, len(phone) - 4)
        prefix = phone[:keep_prefix]
        suffix = phone[-2:]
        masked_len = max(3, len(phone) - len(prefix) - len(suffix))
        return f"{prefix}{'*' * masked_len}{suffix}"

    @staticmethod
    def _relative_time(dt) -> str:
        if not dt:
            return "Aucun"
        now = timezone.now()
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "à l’instant"
        minutes = seconds // 60
        if minutes < 60:
            return f"il y a {minutes} min"
        hours = minutes // 60
        if hours < 24:
            return f"il y a {hours} h"
        days = hours // 24
        if days < 30:
            return f"il y a {days} j"
        months = days // 30
        return f"il y a {months} mois"

    @staticmethod
    def _status_from_signals(qs) -> Tuple[str, str]:
        """
        Simplified & mandatory statuses:
        - 🟢 FIABLE: no recent severe negative pattern
        - 🟠 À SURVEILLER: some serious negatives / instability
        - 🔴 DÉCONSEILLÉ: severe problems reported by multiple owners
        """
        if not qs.exists():
            return TrustReputationService.STATUS_GREEN

        # Distinct owner based thresholds (anti-abuse)
        owners_recommend_no = qs.filter(recommend=False).values("owner_id").distinct().count()
        owners_severity_grave = qs.filter(severity=DriverSignal.Severity.GRAVE).values("owner_id").distinct().count()

        if owners_recommend_no >= 2 or owners_severity_grave >= 2:
            return TrustReputationService.STATUS_RED

        if owners_recommend_no >= 1 or qs.filter(severity__gte=DriverSignal.Severity.SERIOUS).exists():
            return TrustReputationService.STATUS_ORANGE

        return TrustReputationService.STATUS_GREEN

    @staticmethod
    def _top_reasons(qs, limit: int = 2) -> List[str]:
        if not qs.exists():
            return ["Aucun signalement pour le moment.", "—"]

        counts = (
            qs.values("main_problem_code")
            .annotate(c=Count("id"))
            .order_by("-c")[:limit]
        )
        reasons = []
        for row in counts:
            code = row["main_problem_code"]
            label = DriverSignal.MainProblem(code).label if code in DriverSignal.MainProblem.values else "Autre"
            reasons.append(label)
        while len(reasons) < limit:
            reasons.append("—")
        return reasons

    @staticmethod
    def _driver_name_from_profile(profile: Optional[DriverProfile]) -> str:
        if not profile:
            return "(non renseigné)"
        return profile.display_name or "(non renseigné)"

    def summarize(self, permit_number: str) -> Tuple[bool, Optional[TrustReputationSummary], str]:
        """
        Validate permit, aggregate reputation by fingerprint, and optionally enrich name from DriverProfile if exists.
        """
        is_valid, normalized, error = IdentityService.normalize_and_validate_permit(permit_number)
        if not is_valid:
            return False, None, error

        fp = self.fingerprint_permit(normalized)
        qs = DriverSignal.objects.filter(permit_fingerprint=fp).order_by("-created_at")

        profile = IdentityService.search_by_permit(permit_number)

        status_emoji, status_label = self._status_from_signals(qs)
        distinct_owners = qs.values("owner_id").distinct().count()
        top_reasons = self._top_reasons(qs, limit=2)
        last_dt = qs.aggregate(last=Max("created_at"))["last"]
        last_relative = self._relative_time(last_dt)

        grave = qs.filter(severity=DriverSignal.Severity.GRAVE).count()
        serious = qs.filter(severity=DriverSignal.Severity.SERIOUS).count()
        positive = qs.filter(recommend=True).count()

        summary = TrustReputationSummary(
            driver_name=self._driver_name_from_profile(profile),
            masked_permit=DriverProfile.mask_permit(normalized),
            status_emoji=status_emoji,
            status_label=status_label,
            distinct_owners=distinct_owners,
            top_reasons=top_reasons,
            last_signal_relative=last_relative,
            details=(grave, serious, positive),
        )
        return True, summary, ""

