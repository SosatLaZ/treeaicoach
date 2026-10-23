"""Microphone choice and diagnosis (PortAudio via ``sounddevice``), so « le coach ne m'entend pas »
always ends with a French explanation:

* :func:`default_input_name` = the Windows default RECORDING device (WASAPI's default input, matched to
  its MME name: MME names are cut at 31 characters), not PortAudio's « Mappeur de sons » guess;
* :func:`measure` opens one device ~1 s and returns its peak (dBFS) / whether every sample was 0;
* all-zero buffers = Windows microphone privacy blocking (or a muted device); a PortAudio "device
  unavailable / in use" = another app holds it in EXCLUSIVE mode — :func:`explain` turns both into French;
* :func:`probe_all` measures every input and :func:`pick_best` keeps the one with real signal.

``sd`` (the sounddevice module) is injectable everywhere for the tests. Never raises."""

from __future__ import annotations

import logging
import math
import sys
from typing import Any

log = logging.getLogger(__name__)

SILENT_DBFS = -60.0              # peak under this while the player talks: no usable signal
PROBE_S = 1.0

PRIVACY_MSG = ("Windows bloque le micro (tout le son reçu est à zéro). Ouvre Paramètres > Confidentialité > "
               "Microphone et active « Autoriser l'accès au microphone » ET « Autoriser les applications de bureau "
               "à accéder au microphone », puis relance le test.")
EXCLUSIVE_MSG = ("Le micro est pris en mode exclusif par une autre application. Dans Panneau de configuration > Son > "
                 "Enregistrement > ton micro > Propriétés > Avancé, décoche « Autoriser les applications à prendre "
                 "le contrôle exclusif de ce périphérique », ou ferme l'autre application.")
SILENT_MSG = ("Ce micro ne capte presque rien (niveau {db:.0f} dB). Micro coupé (bouton du casque), volume à 0 "
              "dans Windows (Son > Enregistrement), ou ce n'est pas le bon micro.")


def _sd(sd: Any = None) -> Any:
    if sd is not None:
        return sd
    import sounddevice  # noqa: PLC0415

    return sounddevice


def dbfs(peak: float) -> float:
    return 20 * math.log10(max(float(peak), 1.0) / 32768.0)


def _api_names(sd: Any) -> list[str]:
    try:
        return [str(a.get("name", "")) for a in sd.query_hostapis()]
    except Exception:
        return []


def list_inputs(sd: Any = None) -> list[dict]:
    """Input devices ``{index, name, api, rate, channels}``; on Windows the MME entries only (one per
    device, every name the user knows), the « Mappeur de sons » excluded."""
    out: list[dict] = []
    try:
        sd = _sd(sd)
        apis = _api_names(sd)
        seen: set[str] = set()
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0) or 0) <= 0:
                continue
            h = int(d.get("hostapi", -1))
            api = apis[h] if 0 <= h < len(apis) else ""
            name = str(d.get("name") or "").strip()
            if sys.platform == "win32" and api != "MME":
                continue
            low = name.lower()
            if not name or name in seen or "mappeur" in low or "mapper" in low:
                continue
            seen.add(name)
            out.append({"index": i, "name": name, "api": api, "rate": int(float(d.get("default_samplerate") or 0)),
                        "channels": int(d.get("max_input_channels") or 0)})
    except Exception:
        log.debug("list_inputs failed", exc_info=True)
    return out


def default_input_name(sd: Any = None) -> str:
    """Name (as in :func:`list_inputs`) of the Windows default recording device, "" when unknown."""
    try:
        sd = _sd(sd)
        devs = sd.query_devices()
        apis = sd.query_hostapis()
        cand: list[str] = []
        for a in apis:                                  # WASAPI first: its default = Windows' default
            if "WASAPI" in str(a.get("name", "")):
                di = int(a.get("default_input_device", -1))
                if di >= 0:
                    cand.append(str(devs[di].get("name") or ""))
        try:
            di = sd.default.device[0]
            if di is not None and int(di) >= 0:
                cand.append(str(devs[int(di)].get("name") or ""))
        except Exception:
            pass
        names = [d["name"] for d in list_inputs(sd)]
        for c in cand:
            c = c.strip()
            if not c or "mappeur" in c.lower() or "mapper" in c.lower():
                continue
            for n in names:                             # MME names are truncated to 31 chars
                if n == c or (len(n) >= 20 and c.startswith(n)) or (len(c) >= 20 and n.startswith(c)):
                    return n
    except Exception:
        log.debug("default_input_name failed", exc_info=True)
    return ""


def resolve(name: str, sd: Any = None) -> tuple[Any, str]:
    """``(device index or None, shown name)`` for the configured mic ("" = Windows default)."""
    ins = list_inputs(sd)
    if name:
        for d in ins:
            if d["name"] == name:
                return d["index"], name
    dn = default_input_name(sd)
    for d in ins:
        if d["name"] == dn:
            return d["index"], dn
    return None, dn or "micro par défaut"


def classify_error(exc: BaseException | str) -> str:
    """"exclusive" (device busy / exclusive mode), "privacy" (access denied), "missing", "other"."""
    low = str(exc).lower()
    if any(k in low for k in ("-9985", "unavailable", "in use", "exclusive", "audclnt_e_device_in_use",
                              "busy")):
        return "exclusive"
    if any(k in low for k in ("access", "denied", "e_accessdenied", "0x80070005")):
        return "privacy"
    if any(k in low for k in ("invalid device", "-9996", "no device", "not found")):
        return "missing"
    return "other"


def explain(result: dict) -> str:
    """French sentence for a :func:`measure` result ("" = fine)."""
    err = result.get("error")
    if err:
        k = classify_error(err)
        if k == "exclusive":
            return EXCLUSIVE_MSG
        if k == "privacy":
            return PRIVACY_MSG
        return "Micro impossible à ouvrir (débranché ?) : choisis-en un autre dans la liste."
    if result.get("all_zero"):
        return PRIVACY_MSG
    if result.get("db", 0.0) < SILENT_DBFS:
        return SILENT_MSG.format(db=result.get("db", -96.0))
    return ""


def analyse(pcm: bytes) -> tuple[float, bool]:
    """``(peak, all_zero)`` of int16 PCM."""
    try:
        import numpy as np  # noqa: PLC0415

        a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
        if not a.size:
            return 0.0, True
        peak = float(np.abs(a.astype(np.int32)).max())
        return peak, peak == 0.0
    except Exception:
        return 0.0, True


def measure(index: Any, seconds: float = PROBE_S, sd: Any = None, opener: Any = None) -> dict:
    """Open one input ``seconds`` and measure it: ``{name, index, rate, peak, db, all_zero, error}``."""
    res: dict = {"index": index, "rate": 0, "peak": 0.0, "db": -96.0, "all_zero": False, "error": ""}
    mic = None
    try:
        if opener is None:
            from treeaicoach.voice_assistant import SoundDeviceMic  # noqa: PLC0415

            opener = SoundDeviceMic
        mic = opener(index)
        res["rate"] = int(getattr(mic, "rate", 16000) or 16000)
        chunks = []
        for _ in range(max(1, int(seconds / 0.25))):
            b = mic.read()
            if b is None:
                break
            chunks.append(b)
        peak, zero = analyse(b"".join(chunks))
        res.update(peak=peak, db=dbfs(peak), all_zero=zero)
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        if mic is not None:
            try:
                mic.close()
            except Exception:
                pass
    return res


def probe_all(seconds: float = PROBE_S, sd: Any = None, opener: Any = None, skip: Any = None) -> list[dict]:
    """:func:`measure` every input (except ``skip`` index); each result carries its ``name``."""
    out = []
    for d in list_inputs(sd):
        if skip is not None and d["index"] == skip:
            continue
        r = measure(d["index"], seconds, sd, opener)
        r["name"] = d["name"]
        log.info("Micro sondé : « %s » %d Hz, pic %.0f dB%s%s", d["name"], r["rate"], r["db"],
                 " (tout à zéro)" if r["all_zero"] else "", f" erreur {r['error'][:60]}" if r["error"] else "")
        out.append(r)
    return out


def pick_best(results: list[dict]) -> dict | None:
    """The loudest input with a real signal (peak >= :data:`SILENT_DBFS`), None when none has one."""
    ok = [r for r in results if not r.get("error") and not r.get("all_zero") and r.get("db", -96) >= SILENT_DBFS]
    return max(ok, key=lambda r: r["db"]) if ok else None
