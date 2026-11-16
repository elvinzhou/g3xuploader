"""
Builders that render pipeline results into NotificationEvents.

These functions are pure: they take data the auto flows already computed
and return a ready-to-send event, so message formatting is unit-testable
without touching the pipeline itself.
"""

from typing import Any, Dict, List, Optional

from avcardtool.notifications.base import NotificationEvent, Severity


def _fmt_hours(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "?"


def _fmt_time(ts: Optional[str]) -> Optional[str]:
    """Trim an ISO-8601 timestamp down to HH:MM for compact display."""
    if not ts:
        return None
    if len(ts) >= 16 and ts[10] == "T":
        return ts[11:16]
    return ts


def _fmt_date(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    return ts[:10] if len(ts) >= 10 else ts


def flight_entry_from_summary(
    file_name: str,
    summary: Dict[str, Any],
    uploads: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Extract the notification-relevant fields from an analysis summary."""
    hobbs = summary.get("hobbs") or {}
    tach = summary.get("tach") or {}
    oooi = summary.get("oooi") or {}
    return {
        "file": file_name,
        "aircraft": summary.get("aircraft_ident"),
        "hobbs_increment": hobbs.get("increment_hours"),
        "hobbs_ending": hobbs.get("ending_hours"),
        "tach_increment": tach.get("increment_hours"),
        "tach_ending": tach.get("ending_hours"),
        "off_time": oooi.get("off_time"),
        "on_time": oooi.get("on_time"),
        "flight_time_minutes": oooi.get("flight_time_minutes"),
        "uploads": uploads or {},
    }


def build_flights_processed_event(
    stats: Dict[str, int],
    flights: List[Dict[str, Any]],
    notes: Optional[List[str]] = None,
    errors: Optional[List[str]] = None,
    uploads_skipped: bool = False,
) -> NotificationEvent:
    """
    Summarize an auto-process run.

    stats is the auto_process() stats dict; flights is a list of
    flight_entry_from_summary() dicts; notes carry extra lines (Carryd
    result, non-flight override adjustments); errors are per-file failures.
    """
    notes = notes or []
    errors = errors or []

    n = len(flights)
    aircraft = next((f.get("aircraft") for f in flights if f.get("aircraft")), None)
    if n:
        title = f"{n} flight{'s' if n != 1 else ''} processed"
        if aircraft:
            title = f"{aircraft}: {title}"
    elif errors:
        title = f"SD card processed — {len(errors)} file error(s)"
    else:
        title = "SD card processed — no new flights"

    lines: List[str] = []
    for f in flights:
        header = f.get("file", "?")
        if f.get("aircraft"):
            header += f" — {f['aircraft']}"
        lines.append(header)

        times = []
        off = _fmt_time(f.get("off_time"))
        on = _fmt_time(f.get("on_time"))
        if off:
            times.append(f"off {off}")
        if on:
            times.append(f"on {on}")
        if isinstance(f.get("flight_time_minutes"), (int, float)):
            times.append(f"flight {f['flight_time_minutes'] / 60.0:.1f}h")
        if times:
            lines.append("  " + "   ".join(times))

        lines.append(
            f"  Hobbs +{_fmt_hours(f.get('hobbs_increment'))} → {_fmt_hours(f.get('hobbs_ending'))}"
            f"    Tach +{_fmt_hours(f.get('tach_increment'))} → {_fmt_hours(f.get('tach_ending'))}"
        )

        uploads = f.get("uploads") or {}
        if uploads:
            marks = []
            for service, result in uploads.items():
                if result.get("success"):
                    marks.append(f"{service} ✓")
                else:
                    reason = result.get("message") or "failed"
                    marks.append(f"{service} ✗ ({reason})")
            lines.append("  Uploads: " + ", ".join(marks))
        lines.append("")

    for note in notes:
        lines.append(note)
    if notes:
        lines.append("")

    if errors:
        lines.append("File errors:")
        for err in errors:
            lines.append(f"  ✗ {err}")
        lines.append("")

    skipped_bits = []
    if stats.get("already_processed"):
        skipped_bits.append(f"{stats['already_processed']} already processed")
    if stats.get("non_flights"):
        skipped_bits.append(f"{stats['non_flights']} non-flight")
    if skipped_bits:
        lines.append("Skipped: " + ", ".join(skipped_bits))

    if uploads_skipped:
        lines.append("Uploads: skipped")
    else:
        lines.append(
            f"Uploads: {stats.get('upload_success', 0)} succeeded, "
            f"{stats.get('upload_failed', 0)} failed"
        )

    severity = Severity.INFO
    if stats.get("upload_failed") or errors:
        severity = Severity.WARNING

    return NotificationEvent(
        event_type="flights_processed",
        severity=severity,
        title=title,
        body="\n".join(lines).strip(),
        data={"stats": dict(stats), "flights": flights, "errors": errors},
    )


def build_navdata_updated_event(
    card_serial: str,
    avionics: str,
    aircraft: Optional[str],
    installed: List[Dict[str, Any]],
    already_current: Optional[List[Dict[str, str]]] = None,
    file_count: int = 0,
) -> NotificationEvent:
    """
    Announce databases installed to one card.

    installed entries: {"database", "old_issue", "new_issue", "effective_at",
    "upcoming"}; already_current entries: {"database", "issue"}.
    """
    already_current = already_current or []
    now_installed = [d for d in installed if not d.get("upcoming")]
    preloaded = [d for d in installed if d.get("upcoming")]

    if len(installed) == 1:
        d = installed[0]
        what = f"{d['database']} {d['new_issue']}"
        if d.get("upcoming"):
            what += " pre-loaded"
        else:
            what += " installed"
    else:
        what = f"{len(installed)} databases installed"
    title = what + (f" — {aircraft}" if aircraft else "")

    header = f"Card {card_serial} — {avionics}"
    if aircraft:
        header += f" on {aircraft}"
    lines = [header, ""]

    if now_installed:
        lines.append("Installed:")
        for d in now_installed:
            line = f"  {d['database']}: {d.get('old_issue') or 'none'} → {d['new_issue']}"
            effective = _fmt_date(d.get("effective_at"))
            if effective:
                line += f"   (effective {effective})"
            lines.append(line)
    if preloaded:
        lines.append("Pre-loaded upcoming cycle:")
        for d in preloaded:
            line = f"  {d['database']}: {d['new_issue']}"
            effective = _fmt_date(d.get("effective_at"))
            if effective:
                line += f"   (effective {effective})"
            lines.append(line)
    if already_current:
        lines.append("Already current:")
        for d in already_current:
            lines.append(f"  {d['database']} ({d['issue']})")

    lines.append("")
    if file_count:
        lines.append(f"{file_count} file(s) written. Card is ready to fly.")
    else:
        lines.append("Card is ready to fly.")

    return NotificationEvent(
        event_type="navdata_updated",
        severity=Severity.INFO,
        title=title,
        body="\n".join(lines),
        data={
            "card_serial": card_serial,
            "avionics": avionics,
            "aircraft": aircraft,
            "installed": installed,
            "already_current": already_current,
            "file_count": file_count,
        },
    )


def build_navdata_update_failed_event(issues: List[Any]) -> NotificationEvent:
    """Summarize a navdata auto-update run that hit errors.

    issues is the auto-update (severity, message) list; WARNINGs ride along
    in the body but only ERRORs trigger this event.
    """
    error_msgs = [msg for sev, msg in issues if sev == "ERROR"]
    warning_msgs = [msg for sev, msg in issues if sev != "ERROR"]

    title = f"Navdata update failed ({len(error_msgs)} error{'s' if len(error_msgs) != 1 else ''})"
    lines = ["The automatic navigation database update did not complete:", ""]
    for msg in error_msgs:
        lines.append(f"  ERROR: {msg}")
    for msg in warning_msgs:
        lines.append(f"  WARNING: {msg}")
    lines += ["", "Details: journalctl -u 'avcardtool-navdata*' -n 200"]

    return NotificationEvent(
        event_type="navdata_update_failed",
        severity=Severity.ERROR,
        title=title,
        body="\n".join(lines),
        data={"errors": error_msgs, "warnings": warning_msgs},
    )


def build_garmin_auth_expired_event() -> NotificationEvent:
    return NotificationEvent(
        event_type="garmin_auth_expired",
        severity=Severity.ERROR,
        title="Garmin login expired — database updates stopped",
        body=(
            "Automatic database updates are stopped. Cards will NOT receive\n"
            "new cycles until you re-authenticate:\n"
            "\n"
            "    avcardtool navdata login\n"
        ),
    )


def build_processing_error_event(message: str) -> NotificationEvent:
    return NotificationEvent(
        event_type="processing_error",
        severity=Severity.ERROR,
        title="Flight log processing failed",
        body=message,
        data={"message": message},
    )


def build_firmware_available_event(
    product_name: str,
    version: str,
    release_date: str,
    notes: List[str],
    page_url: str,
    cache_path: str,
    bulletin_path: Optional[str] = None,
    auto_install: bool = True,
    max_notes: int = 15,
    baseline_hint: Optional[str] = None,
) -> NotificationEvent:
    """Announce a new avionics software version that is now in the local cache."""
    title = f"{product_name} software {version} available"
    lines = [f"Garmin has published {product_name} software version {version}"
             + (f" (as of {release_date})." if release_date else "."), ""]
    if notes:
        lines.append("Notes from Garmin:")
        for n in notes[:max_notes]:
            lines.append(f"  - {n}")
        if len(notes) > max_notes:
            lines.append(f"  ... and {len(notes) - max_notes} more on the page below")
        lines.append("")
    lines.append(f"Page: {page_url}")
    lines.append(f"Downloaded to: {cache_path}")
    if bulletin_path:
        lines.append(f"Service bulletin: {bulletin_path}")
        lines.append("Garmin's public bulletin is addressed to non-certified aircraft; check that it applies to yours.")
    lines.append("")
    if baseline_hint:
        lines.append(baseline_hint)
        lines.append("")
    if auto_install:
        lines.append("Cards that qualify are updated automatically when inserted; you will be told when one is written.")
    else:
        lines.append("Automatic card installs are off (firmware.auto_install); use 'avcardtool firmware install'.")

    return NotificationEvent(
        event_type="firmware_available",
        severity=Severity.INFO,
        title=title,
        body="\n".join(lines),
        data={
            "product": product_name,
            "version": version,
            "release_date": release_date,
            "page_url": page_url,
            "cache_path": cache_path,
            "bulletin_path": bulletin_path,
        },
    )


def build_firmware_card_written_event(
    product_name: str,
    version: str,
    card: str,
    file_count: int,
    next_steps: List[str],
    installed_version: Optional[str] = None,
) -> NotificationEvent:
    """Announce that firmware was written to one card. ``card`` is its mount point / volume id."""
    title = f"{product_name} software {version} written to card"
    lines = [f"{product_name} software {version} was written to card {card} ({file_count} file(s), verified)."]
    if installed_version:
        lines.append(f"Previously on the card/display: {installed_version}")
    lines += ["", "Next:"]
    lines += [f"  - {step}" for step in next_steps]

    return NotificationEvent(
        event_type="firmware_card_written",
        severity=Severity.INFO,
        title=title,
        body="\n".join(lines),
        data={
            "product": product_name,
            "version": version,
            "card": card,
            "file_count": file_count,
            "installed_version": installed_version,
        },
    )


def build_firmware_update_failed_event(issues: List[Any]) -> NotificationEvent:
    """Summarize a firmware run that hit errors ((severity, message) pairs, like navdata)."""
    error_msgs = [msg for sev, msg in issues if sev == "ERROR"]
    warning_msgs = [msg for sev, msg in issues if sev != "ERROR"]

    title = f"Firmware update failed ({len(error_msgs)} error{'s' if len(error_msgs) != 1 else ''})"
    lines = ["The automatic avionics firmware update did not complete:", ""]
    for msg in error_msgs:
        lines.append(f"  ERROR: {msg}")
    for msg in warning_msgs:
        lines.append(f"  WARNING: {msg}")
    lines += ["", "Details: journalctl -u 'avcardtool-navdata*' -n 200"]

    return NotificationEvent(
        event_type="firmware_update_failed",
        severity=Severity.ERROR,
        title=title,
        body="\n".join(lines),
        data={"errors": error_msgs, "warnings": warning_msgs},
    )


def build_firmware_confirm_needed_event(
    product_name: str,
    version: str,
    link: Optional[str],
    confirm_command: str,
    cancel_command: str,
    bulletin_path: Optional[str] = None,
    reminder: bool = False,
) -> NotificationEvent:
    """
    Ask the pilot to confirm that the firmware written to the avionics' own database
    card was installed. The card itself is deliberately not named here.
    """
    title = f"{product_name} software {version} is on the card — confirm when installed"
    if reminder:
        title = "Reminder: " + title
    lines = [
        f"The database card now holds only the {product_name} firmware loader (version {version}). "
        "Its databases were removed and will be reinstalled after you confirm.",
        "",
        f"The {product_name} starts in configuration mode with this card inserted: follow Garmin's "
        "service bulletin to load the software.",
    ]
    if bulletin_path:
        lines.append(f"Service bulletin: {bulletin_path}")
    lines.append("")
    if link:
        lines += ["When the software is installed, open this link and confirm:", f"  {link}",
                  "(Anyone with the link can confirm; it works once.)"]
    else:
        lines += ["When the software is installed, run on the Raspberry Pi:", f"  {confirm_command}",
                  "or, if you did not install it:", f"  {cancel_command}"]
    lines += ["", "Database updates for this card are paused until you confirm or cancel."]
    return NotificationEvent(
        event_type="firmware_confirm_needed",
        severity=Severity.WARNING,
        title=title,
        body="\n".join(lines),
        data={"product": product_name, "version": version, "reminder": reminder},
    )
