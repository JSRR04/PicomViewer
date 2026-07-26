# -*- coding: utf-8 -*-
"""
PycomViewer  –  DICOM / MRT Viewer (laienfreundlich)
====================================================
Abhaengigkeiten:
    pip install customtkinter pydicom pillow numpy

Unterstuetzte Eingaben
----------------------
* Einzelne DICOM-Dateien (.dcm)
* Extensionslose / IMA-Dateien im CD-Baum  (z. B. .../PAT1/STU1/SER7/IMA8)
* DICOMDIR-Indexdateien (Datei direkt waehlen ODER Ordner, der eine enthaelt)
* Multi-Frame-Dateien (eine Datei, viele Schichten)

Die Datei "terminology.json" (gleicher Ordner) enthaelt alle Begriffe in drei
umschaltbaren Sprachmodi: Einfach / Medizinisch / English. Frei editierbar;
fehlt sie, greift eine eingebaute Notfassung.

Bedienung
---------
* Oben "Untersuchung oeffnen" -> Ordner (auch DICOMDIR/IMA) wird geladen.
* Links erscheinen die Serien als Vorschau -> in ein Feld ziehen oder doppelklicken.
* "Was moechtest du sehen?" -> Vorne / Seite / Oben (coronar / sagittal / axial).
* Mausrad = Schichten blaettern | Strg+Mausrad = Zoom | rechte Maustaste = Kontrast.
"""

import os
import json
import copy
import logging
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import pydicom
import customtkinter as ctk
from PIL import Image, ImageTk

from pydicom.tag import Tag
from pydicom.uid import generate_uid

try:                                              # pydicom-Kompatibilitaet
    from pydicom.pixels import apply_modality_lut          # noqa: F401
except Exception:
    from pydicom.pixel_data_handlers.util import apply_modality_lut  # noqa: F401

try:
    from pydicom.fileset import FileSet
except Exception:
    FileSet = None


# --------------------------------------------------------------------------- #
#  Logging / Debugging
# --------------------------------------------------------------------------- #
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pycomviewer.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")],
)
LOG = logging.getLogger("PycomViewer")

# Transfer-Syntaxen, die einen externen Decoder benoetigen
COMPRESSED_TS = {
    "1.2.840.10008.1.2.4.50": "JPEG Baseline",
    "1.2.840.10008.1.2.4.51": "JPEG Extended",
    "1.2.840.10008.1.2.4.57": "JPEG Lossless",
    "1.2.840.10008.1.2.4.70": "JPEG Lossless SV1",
    "1.2.840.10008.1.2.4.80": "JPEG-LS Lossless",
    "1.2.840.10008.1.2.4.81": "JPEG-LS Near-Lossless",
    "1.2.840.10008.1.2.4.90": "JPEG 2000 Lossless",
    "1.2.840.10008.1.2.4.91": "JPEG 2000",
    "1.2.840.10008.1.2.5":    "RLE Lossless",
}
DECODER_HINT = ("Bilder sind komprimiert. Bitte einen Decoder installieren:\n"
                "    pip install pylibjpeg pylibjpeg-libjpeg pylibjpeg-openjpeg python-gdcm")


def available_decoders():
    """Tatsaechlich importierbare Pixel-Decoder-Plugins auflisten."""
    found = []
    for label, mod in (("gdcm", "gdcm"), ("pylibjpeg", "pylibjpeg"),
                       ("libjpeg", "libjpeg"), ("openjpeg", "openjpeg"), ("pillow", "PIL")):
        try:
            __import__(mod); found.append(label)
        except Exception:
            pass
    return found


def has_compressed_decoder():
    """True, wenn komprimierte DICOMs (JPEG/JPEG2000/JPEG-LS) dekodiert werden koennen."""
    dec = available_decoders()
    if "gdcm" in dec:
        return True
    return "pylibjpeg" in dec and ("libjpeg" in dec or "openjpeg" in dec)


# --------------------------------------------------------------------------- #
#  Design
# --------------------------------------------------------------------------- #
ACCENT       = "#0891b2"
ACCENT_HOVER = "#0e7490"
PANEL_BG     = "#0b0f14"
CANVAS_BG    = "#05070a"
SUBTLE       = "#1f2937"
SIDEBAR_BG   = "#0a0e13"
TEXT_MUTED   = "#94a3b8"
GOOD         = "#22c55e"

MODE_ORDER   = ["simple", "med_de", "med_en"]

WINDOW_PRESETS = {
    "auto": None, "weichteil": (40, 400), "lunge": (-600, 1500),
    "knochen": (300, 1500), "gehirn": (40, 80), "leber": (60, 160), "mrt": (500, 1000),
}
PRESET_LABELS = {
    "auto": {"simple": "Automatisch", "med_de": "Auto (Slice)", "med_en": "Auto"},
    "weichteil": {"simple": "Weichteile", "med_de": "Weichteil (CT)", "med_en": "Soft tissue"},
    "lunge": {"simple": "Lunge", "med_de": "Lunge (CT)", "med_en": "Lung"},
    "knochen": {"simple": "Knochen", "med_de": "Knochen (CT)", "med_en": "Bone"},
    "gehirn": {"simple": "Kopf/Gehirn", "med_de": "Gehirn (CT)", "med_en": "Brain"},
    "leber": {"simple": "Bauch/Leber", "med_de": "Leber (CT)", "med_en": "Liver"},
    "mrt": {"simple": "MRT breit", "med_de": "MRT (breit)", "med_en": "MRI (wide)"},
}
TOOL_IDS = ["pan", "wl", "length", "rect", "ellipse", "arrow", "probe"]

EMBEDDED_TERMS = {
    "modes": {"simple": "Einfach", "med_de": "Medizinisch", "med_en": "English"},
    "planes": {p: {"label": {"simple": p, "med_de": p, "med_en": p},
                   "explain": {"simple": "", "med_de": "", "med_en": ""}}
               for p in ("axial", "coronal", "sagittal", "oblique", "unknown")},
    "directions": {d: {"simple": d, "med_de": d, "med_en": d}
                   for d in ("H", "F", "A", "P", "L", "R")},
    "views": {"front": {"plane": "coronal", "label": {"simple": "Vorne", "med_de": "Koronar", "med_en": "Coronal"}},
              "side": {"plane": "sagittal", "label": {"simple": "Seite", "med_de": "Sagittal", "med_en": "Sagittal"}},
              "top": {"plane": "axial", "label": {"simple": "Oben", "med_de": "Axial", "med_en": "Axial"}}},
    "ui": {}, "tools": {t: {"label": {"simple": t, "med_de": t, "med_en": t},
                            "tip": {"simple": "", "med_de": "", "med_en": ""}} for t in TOOL_IDS},
    "glossary": {},
}


# ===========================================================================
#  Terminologie
# ===========================================================================
class Terms:
    def __init__(self):
        self.mode = "simple"
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "terminology.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except Exception:
            self.data = EMBEDDED_TERMS

    def _pick(self, d):
        if not isinstance(d, dict):
            return str(d)
        return d.get(self.mode) or d.get("med_en") or next(iter(d.values()), "")

    def ui(self, key):          return self._pick(self.data.get("ui", {}).get(key, {})) or key
    def plane_label(self, p):   return self._pick(self.data["planes"].get(p, {}).get("label", {})) or p
    def plane_explain(self, p): return self._pick(self.data["planes"].get(p, {}).get("explain", {}))
    def direction(self, l):     return self._pick(self.data["directions"].get(l, {})) or l
    def tool_label(self, t):    return self._pick(self.data["tools"].get(t, {}).get("label", {})) or t
    def tool_tip(self, t):      return self._pick(self.data["tools"].get(t, {}).get("tip", {}))
    def preset_label(self, k):  return self._pick(PRESET_LABELS.get(k, {})) or k
    def view_label(self, k):    return self._pick(self.data["views"].get(k, {}).get("label", {})) or k
    def view_plane(self, k):    return self.data["views"].get(k, {}).get("plane", "")

    def glossary_items(self):
        return [(self._pick(v.get("term", {})), self._pick(v.get("text", {})))
                for v in self.data.get("glossary", {}).values()]


# ===========================================================================
#  Orientierung
# ===========================================================================
def _dir_letter(vec):
    labels = [("R", "L"), ("A", "P"), ("F", "H")]     # LPS: +X=L, +Y=P, +Z=H
    i = int(np.argmax(np.abs(vec)))
    neg, pos = labels[i]
    return pos if vec[i] >= 0 else neg


def analyze_orientation(ds):
    iop = getattr(ds, "ImageOrientationPatient", None)
    if iop is None or len(iop) != 6:
        return "unknown", None
    row = np.array([float(x) for x in iop[:3]])
    col = np.array([float(x) for x in iop[3:]])
    normal = np.cross(row, col)
    plane = ["sagittal", "coronal", "axial"][int(np.argmax(np.abs(normal)))]
    if np.max(np.abs(normal)) < 0.80:
        plane = "oblique"
    edges = {"right": _dir_letter(row), "left": _dir_letter(-row),
             "bottom": _dir_letter(col), "top": _dir_letter(-col)}
    return plane, edges


# ===========================================================================
#  Datenmodell
# ===========================================================================
class DicomSeries:
    def __init__(self, uid, desc, vol, dsets, modality, multiframe, source):
        self.uid, self.desc = uid, (desc or "(ohne Beschreibung)")
        self.vol, self.dsets = vol, dsets
        self.modality, self.multiframe, self.source = modality, multiframe, source
        self.n = vol.shape[0]
        self.is_color = (vol.ndim == 4)

        ds0 = dsets[0]
        self.slope = float(getattr(ds0, "RescaleSlope", 1) or 1)
        self.inter = float(getattr(ds0, "RescaleIntercept", 0) or 0)
        self.photometric = str(getattr(ds0, "PhotometricInterpretation", "MONOCHROME2"))
        ps = getattr(ds0, "PixelSpacing", None)
        self.pixel_spacing = (float(ps[0]), float(ps[1])) if ps and len(ps) == 2 else None

        if self.is_color:
            self.mod_min, self.mod_max = 0.0, 255.0
        else:
            self.mod_min = float(np.min(vol)) * self.slope + self.inter
            self.mod_max = float(np.max(vol)) * self.slope + self.inter

        self.plane, self.edges = analyze_orientation(dsets[len(dsets) // 2])
        self.default_center, self.default_width = self._default_window(ds0)
        self._thumb = None

    def _default_window(self, ds):
        """DICOM-Fenster aus Tags nutzen, falls plausibel; sonst robustes Auto-Fenster."""
        c = getattr(ds, "WindowCenter", None)
        w = getattr(ds, "WindowWidth", None)
        if c is not None and w is not None:
            try:
                c = float(np.ravel([c])[0]); w = float(np.ravel([w])[0])
            except Exception:
                c = w = None
            if c is not None and w and w > 0:
                span = max(self.mod_max - self.mod_min, 1.0)
                # unplausible Tag-Fenster (viel zu breit / voellig ausserhalb) verwerfen
                if w <= span * 4 and (self.mod_min - span) <= c <= (self.mod_max + span):
                    return c, w
        return self.auto_window(0)

    def auto_window(self, idx):
        """Robustes Auto-Fenster: Hintergrund (Luft) wird ausgeblendet, dann 1./99. Perzentil.

        Deutlich besser fuer MRT/CT als reines min/max oder 2/98 ueber das ganze Bild,
        weil grosse schwarze Hintergrundflaechen das Fenster sonst zu breit machen.
        """
        if self.is_color:
            return 128.0, 256.0
        a = (self.vol[idx].astype(np.float32) * self.slope + self.inter).ravel()
        amin, amax = float(a.min()), float(a.max())
        if amax <= amin:
            return amin, 1.0
        # Vordergrund: Pixel oberhalb eines kleinen Anteils ueber dem Minimum
        thr = amin + 0.05 * (amax - amin)
        fg = a[a > thr]
        if fg.size < max(64, int(0.02 * a.size)):
            fg = a
        p_lo, p_hi = np.percentile(fg, (1.0, 99.0))
        if p_hi <= p_lo:                            # Vordergrund quasi einwertig
            v = float(np.median(fg))
            p_lo, p_hi = v - 1.0, v + 1.0
        center = (p_lo + p_hi) / 2.0
        width = max((p_hi - p_lo) * 1.15, 1.0)     # etwas Reserve -> nicht zu hart
        return float(center), float(width)

    def modality_slice(self, idx):
        a = self.vol[idx].astype(np.float32)
        return a if self.is_color else a * self.slope + self.inter

    def render(self, idx, center, width, invert=False):
        frame = self.vol[idx]
        if self.is_color:
            return Image.fromarray(frame.astype(np.uint8), "RGB")
        a = frame.astype(np.float32) * self.slope + self.inter
        lo, hi = center - width / 2.0, center + width / 2.0
        if hi <= lo:
            hi = lo + 1.0
        out = np.clip((a - lo) * (255.0 / (hi - lo)), 0, 255)
        if self.photometric == "MONOCHROME1":
            invert = not invert
        if invert:
            out = 255.0 - out
        return Image.fromarray(out.astype(np.uint8), "L")

    def thumbnail(self, max_px=190):
        if self._thumb is None or max_px != 190:
            img = self.render(self.n // 2, self.default_center, self.default_width)
            s = max_px / max(img.width, img.height)
            thumb = img.resize((max(int(img.width * s), 1), max(int(img.height * s), 1)))
            if max_px == 190:
                self._thumb = thumb
            return thumb
        return self._thumb


# ===========================================================================
#  Laden:  Datei / Ordner / DICOMDIR / IMA
# ===========================================================================
def _transfer_syntax(ds):
    fm = getattr(ds, "file_meta", None)
    return str(getattr(fm, "TransferSyntaxUID", "")) if fm else ""


def _safe_pixels(ds, errors):
    """pixel_array robust dekodieren; bei Fehler wird die Ursache (inkl. TransferSyntax) protokolliert."""
    try:
        return np.asarray(ds.pixel_array)
    except Exception as ex:
        ts = _transfer_syntax(ds)
        name = COMPRESSED_TS.get(ts, "")
        detail = f" [{name}]" if name else ""
        msg = f"Bild nicht dekodierbar (TransferSyntax {ts or '?'}{detail}): {ex}"
        LOG.warning(msg)
        if errors is not None and msg not in errors:
            errors.append(msg)
        return None


def _read_dataset(path, errors=None):
    """Liest eine DICOM/IMA-Datei (auch ohne Endung) und liefert sie, falls sie ein Bild ist."""
    try:
        ds = pydicom.dcmread(path, force=True)
    except Exception as ex:
        LOG.debug("dcmread fehlgeschlagen: %s (%s)", path, ex)
        return None
    if "PixelData" not in ds:
        LOG.debug("kein PixelData: %s", path)
        return None
    return ds


def load_items_from_dicomdir(dicomdir_path):
    """Alle Bild-Instanzen eines DICOMDIR ueber pydicom.FileSet einlesen."""
    if FileSet is None:
        LOG.warning("pydicom.FileSet nicht verfuegbar – DICOMDIR wird uebersprungen.")
        return []
    items = []
    try:
        fs = FileSet(dicomdir_path)
    except Exception as ex:
        LOG.warning("DICOMDIR konnte nicht geoeffnet werden: %s (%s)", dicomdir_path, ex)
        return []
    for inst in fs:
        try:
            ds = inst.load()
            if "PixelData" in ds:
                items.append((inst.path, ds))
        except Exception as ex:
            LOG.debug("DICOMDIR-Instanz nicht ladbar: %s", ex)
    LOG.info("DICOMDIR: %d Bild-Instanz(en) gelesen aus %s", len(items), dicomdir_path)
    return items


def find_dicomdir(folder):
    for rt, _, files in os.walk(folder):
        for fn in files:
            if fn.upper() == "DICOMDIR":
                return os.path.join(rt, fn)
    return None


def load_items_from_folder(folder):
    """DICOMDIR bevorzugen; sonst rekursiv ALLE Dateien scannen (auch IMA/extensionslos)."""
    LOG.info("Lade Ordner: %s", folder)
    dd = find_dicomdir(folder)
    if dd:
        LOG.info("DICOMDIR gefunden: %s", dd)
        items = load_items_from_dicomdir(dd)
        if items:
            return items, "DICOMDIR"
        LOG.info("DICOMDIR leer/unlesbar – wechsle auf Ordner-Scan.")
    items = []
    scanned = 0
    for rt, _, files in os.walk(folder):
        for fn in files:
            if fn.upper() == "DICOMDIR":
                continue
            scanned += 1
            ds = _read_dataset(os.path.join(rt, fn))
            if ds is not None:
                items.append((os.path.join(rt, fn), ds))
    LOG.info("Ordner-Scan: %d von %d Datei(en) sind DICOM-Bilder.", len(items), scanned)
    return items, "Ordner-Scan"


def build_series_from_datasets(items, errors=None):
    groups = {}
    for path, ds in items:
        nf = int(getattr(ds, "NumberOfFrames", 1) or 1)
        key = str(ds.get("SeriesInstanceUID", path))
        groups.setdefault(key, []).append((path, ds, nf))
    LOG.info("Gruppierung: %d Datei(en) -> %d Serie(n).", len(items), len(groups))

    out = []
    for key, group in groups.items():
        desc = str(group[0][1].get("SeriesDescription", "")) or "(ohne Beschreibung)"
        # Multi-Frame-Einzeldatei
        if len(group) == 1 and group[0][2] > 1:
            path, ds, _ = group[0]
            vol = _safe_pixels(ds, errors)
            if vol is None:
                LOG.warning("Serie '%s' uebersprungen (Multi-Frame nicht dekodierbar).", desc)
                continue
            if vol.ndim == 2:
                vol = vol[np.newaxis, ...]
            out.append(DicomSeries(key, desc, vol, [ds] * vol.shape[0],
                                   str(ds.get("Modality", "")), True, path))
            LOG.info("Serie '%s': Multi-Frame, %d Schicht(en).", desc, vol.shape[0])
            continue

        def skey(t):
            _, ds, _ = t
            inst = getattr(ds, "InstanceNumber", None)
            loc = getattr(ds, "SliceLocation", None)
            return (int(inst) if inst is not None else 0,
                    float(loc) if loc is not None else 0.0)

        group = sorted(group, key=skey)
        frames, dsets = [], []
        for path, ds, _ in group:
            arr = _safe_pixels(ds, errors)
            if arr is not None:
                frames.append(arr); dsets.append(ds)
        if not frames:
            LOG.warning("Serie '%s' uebersprungen (0 von %d Bildern dekodierbar).", desc, len(group))
            continue
        shape0 = frames[0].shape
        keep = [(f, d) for f, d in zip(frames, dsets) if f.shape == shape0]
        if len(keep) < len(frames):
            LOG.info("Serie '%s': %d Bild(er) mit abweichender Groesse ausgelassen.",
                     desc, len(frames) - len(keep))
        vol = np.stack([f for f, _ in keep], axis=0)
        out.append(DicomSeries(key, str(keep[0][1].get("SeriesDescription", "")),
                               vol, [d for _, d in keep],
                               str(keep[0][1].get("Modality", "")), False, group[0][0]))
        LOG.info("Serie '%s': %d Schicht(en), Ebene=%s.", desc, vol.shape[0], out[-1].plane)
    return out


# ===========================================================================
#  Anonymisierung  (angelehnt an DICOM PS3.15 Basic Confidentiality Profile)
# ===========================================================================
# Diese Felder werden ENTFERNT (personenbezogene / einrichtungsbezogene Daten).
# SeriesDescription / StudyDescription / Modality / Geometrie bleiben erhalten,
# damit die Bilder weiter sinnvoll darstellbar sind.
ANON_REMOVE = [
    "PatientBirthDate", "PatientBirthTime", "PatientSex", "PatientAge",
    "PatientSize", "PatientWeight", "PatientAddress", "PatientTelephoneNumbers",
    "PatientTelecomInformation", "PatientMotherBirthName", "PatientBirthName",
    "CountryOfResidence", "RegionOfResidence", "PatientReligiousPreference",
    "PatientComments", "MilitaryRank", "BranchOfService", "EthnicGroup",
    "Occupation", "AdditionalPatientHistory", "PregnancyStatus", "LastMenstrualDate",
    "OtherPatientIDs", "OtherPatientIDsSequence", "OtherPatientNames",
    "IssuerOfPatientID", "PatientInsurancePlanCodeSequence",
    "ReferringPhysicianName", "ReferringPhysicianAddress",
    "ReferringPhysicianTelephoneNumbers", "ReferringPhysicianIdentificationSequence",
    "ConsultingPhysicianName", "PerformingPhysicianName",
    "PerformingPhysicianIdentificationSequence", "NameOfPhysiciansReadingStudy",
    "PhysiciansOfRecord", "PhysiciansOfRecordIdentificationSequence",
    "RequestingPhysician", "OperatorsName", "OperatorIdentificationSequence",
    "InstitutionName", "InstitutionAddress", "InstitutionalDepartmentName",
    "InstitutionCodeSequence", "StationName", "DeviceSerialNumber",
    "AccessionNumber", "StudyID", "AdmissionID", "IssuerOfAdmissionID",
    "RequestAttributesSequence", "PerformedProcedureStepID",
    "PerformedProcedureStepDescription", "ScheduledProcedureStepID",
    "RequestedProcedureID", "RequestedProcedureDescription",
]
# Datumsangaben (optional entfernbar)
ANON_DATES = [
    "StudyDate", "StudyTime", "SeriesDate", "SeriesTime", "AcquisitionDate",
    "AcquisitionTime", "AcquisitionDateTime", "ContentDate", "ContentTime",
    "InstanceCreationDate", "InstanceCreationTime", "PatientBirthDate",
    "OverlayDate", "OverlayTime", "PerformedProcedureStepStartDate",
    "PerformedProcedureStepStartTime", "ScheduledProcedureStepStartDate",
    "DateOfLastCalibration", "TimeOfLastCalibration",
]
STANDARD_UID_PREFIX = "1.2.840.10008"     # DICOM-Standard-UIDs -> NICHT umbenennen
UID_KEEP = {"SOPClassUID", "MediaStorageSOPClassUID", "TransferSyntaxUID",
            "ImplementationClassUID"}


def _new_uid(old, uid_map):
    if old not in uid_map:
        uid_map[old] = pydicom.uid.generate_uid()
    return uid_map[old]


def _regen_uids(dataset, uid_map):
    """Instanz-UIDs konsistent neu vergeben; Standard-UIDs bleiben unveraendert."""
    for el in dataset:
        if el.VR == "UI" and el.keyword not in UID_KEEP:
            v = el.value
            if isinstance(v, (list, pydicom.multival.MultiValue)):
                el.value = [(_new_uid(str(x), uid_map) if x and not str(x).startswith(STANDARD_UID_PREFIX) else x) for x in v]
            elif v and not str(v).startswith(STANDARD_UID_PREFIX):
                el.value = _new_uid(str(v), uid_map)
        elif el.VR == "SQ":
            for item in el.value:
                _regen_uids(item, uid_map)


def anonymize_dataset(ds, opts, uid_map):
    """Ein Dataset in-place anonymisieren."""
    if opts.get("replace_identity", True):
        ds.PatientName = opts.get("name") or "ANONYMOUS"
        ds.PatientID = opts.get("id") or "ANON"
    if opts.get("remove_phi", True):
        for kw in ANON_REMOVE:
            if opts.get("keep_age_sex") and kw in ("PatientAge", "PatientSex"):
                continue
            if kw in ds:
                try:
                    delattr(ds, kw)
                except Exception:
                    pass
    if opts.get("remove_dates", True):
        for kw in ANON_DATES:
            if kw in ds:
                try:
                    delattr(ds, kw)
                except Exception:
                    pass
    if opts.get("remove_private", True):
        try:
            ds.remove_private_tags()
        except Exception:
            pass
    if opts.get("regen_uids", True):
        _regen_uids(ds, uid_map)
        if getattr(ds, "file_meta", None) is not None:
            _regen_uids(ds.file_meta, uid_map)
    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = "PycomViewer basic profile"
    return ds


def _save_ds(ds, path):
    try:
        ds.save_as(path, enforce_file_format=True)
    except TypeError:
        ds.save_as(path)


def _slug(text, maxlen=24):
    keep = "".join(c if c.isalnum() or c in "-_ " else "_" for c in str(text))
    return keep.strip().replace(" ", "_")[:maxlen] or "series"


def _unique_datasets(series):
    """Multi-Frame-Serien teilen sich ein Dataset -> Duplikate ueberspringen."""
    seen, out = set(), []
    for ds in series.dsets:
        if id(ds) not in seen:
            seen.add(id(ds)); out.append(ds)
    return out


def anonymize_and_export(series_list, opts, out_dir, update_loaded=False):
    """Serien anonymisieren und als DICOM-Dateien in out_dir schreiben. Gibt Anzahl zurueck."""
    uid_map = {}
    n = 0
    for si, series in enumerate(series_list):
        for ds in _unique_datasets(series):
            target = ds if update_loaded else copy.deepcopy(ds)
            anonymize_dataset(target, opts, uid_map)
            path = os.path.join(out_dir, f"anon_{si + 1:02d}_{n + 1:04d}.dcm")
            _save_ds(target, path)
            n += 1
    LOG.info("Anonymisiert & exportiert: %d Datei(en) nach %s", n, out_dir)
    return n


def export_series(series, out_dir):
    """Serie unveraendert als Einzeldateien exportieren. Gibt Anzahl zurueck."""
    n = 0
    for ds in _unique_datasets(series):
        _save_ds(ds, os.path.join(out_dir, f"{_slug(series.desc)}_{n + 1:04d}.dcm"))
        n += 1
    LOG.info("Serie '%s' exportiert: %d Datei(en) nach %s", series.desc, n, out_dir)
    return n


# ===========================================================================
#  Tooltip
# ===========================================================================
class Tooltip:
    def __init__(self, widget, text_fn):
        self.widget, self.text_fn, self.tip = widget, text_fn, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, e):
        text = self.text_fn()
        if not text:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True); self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=text, bg="#111827", fg="#e5e7eb", font=("", 10),
                 justify="left", wraplength=280, padx=8, pady=5,
                 relief="solid", borderwidth=1).pack()
        self.tip.geometry(f"+{e.x_root + 14}+{e.y_root + 14}")

    def _hide(self, _):
        if self.tip:
            self.tip.destroy(); self.tip = None


# ===========================================================================
#  Vorschau-Kachel (ziehbar)
# ===========================================================================
class ThumbnailWidget(ctk.CTkFrame):
    def __init__(self, master, app, series):
        super().__init__(master, fg_color=PANEL_BG, corner_radius=6)
        self.app, self.series = app, series
        pil = series.thumbnail()
        self.ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(pil.width, pil.height))
        self.img_label = ctk.CTkLabel(self, image=self.ctk_img, text="")
        self.img_label.pack(padx=6, pady=(6, 2))
        plane = app.terms.plane_label(series.plane)
        self.txt = ctk.CTkLabel(self, text=f"{series.desc}\n{series.modality} · {series.n} · {plane}",
                                font=("", 11), text_color=TEXT_MUTED, justify="left")
        self.txt.pack(padx=6, pady=(0, 6))
        for w in (self, self.img_label, self.txt):
            w.bind("<Button-1>", self._press, add="+")
            w.bind("<B1-Motion>", self._drag, add="+")
            w.bind("<ButtonRelease-1>", self._release, add="+")
            w.bind("<Double-Button-1>", self._dbl, add="+")
        # kleiner Entfernen-Button oben rechts
        self.remove_btn = ctk.CTkButton(self, text="×", width=22, height=22,
                                        fg_color="#7f1d1d", hover_color="#991b1b",
                                        command=self._remove)
        self.remove_btn.place(relx=1.0, x=-4, y=4, anchor="ne")
        Tooltip(self, lambda: self.app.terms.plane_explain(series.plane))

    def _press(self, e):    self.app.begin_drag(self.series, e)
    def _drag(self, e):     self.app.update_drag(e)
    def _release(self, e):  self.app.end_drag(e)
    def _remove(self):      self.app.remove_series(self.series)
    def _dbl(self, e):
        if self.app.active_panel:
            self.app.active_panel.assign_series(self.series)


# ===========================================================================
#  Viewport
# ===========================================================================
class ImagePanel(ctk.CTkFrame):
    def __init__(self, master, app, **kw):
        super().__init__(master, fg_color=PANEL_BG, corner_radius=6, **kw)
        self.app = app
        self.series = None
        self.slice_idx = 0
        self.center = self.width = 0.0
        self.invert = False
        self.zoom = 1.0
        self.offset = [0, 0]
        self.tk_image = None
        self.annotations = {}
        self._drag_start = None
        self._temp = []

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=6, pady=(6, 0))
        self.title = ctk.CTkLabel(head, text="—", font=("", 12), text_color=TEXT_MUTED)
        self.title.pack(side="left")
        self.clear_btn = ctk.CTkButton(head, text="×", width=24, height=20, fg_color=SUBTLE,
                                       hover_color="#7f1d1d", command=self.clear_panel)
        self.clear_btn.pack(side="right", padx=(6, 0))
        self.slice_label = ctk.CTkLabel(head, text="", font=("", 12), text_color=TEXT_MUTED)
        self.slice_label.pack(side="right")

        self.canvas = tk.Canvas(self, bg=CANVAS_BG, highlightthickness=1, highlightbackground=SUBTLE)
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        c = self.canvas
        c.bind("<Button-1>", self._l_down); c.bind("<B1-Motion>", self._l_drag); c.bind("<ButtonRelease-1>", self._l_up)
        c.bind("<Button-3>", self._r_down); c.bind("<B3-Motion>", self._r_drag)
        c.bind("<Motion>", self._motion)
        c.bind("<MouseWheel>", self._wheel); c.bind("<Button-4>", self._wheel); c.bind("<Button-5>", self._wheel)
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<Enter>", lambda e: self.app.set_active_panel(self))

    def assign_series(self, series):
        self.series = series; self.slice_idx = 0; self.annotations = {}
        self.center, self.width = series.default_center, series.default_width
        self.invert = False
        self.fit_to_window(); self.redraw()
        self.app.set_active_panel(self); self.app.sync_controls_from_active(); self.app.update_info_panel()

    def clear_panel(self):
        """Panel leeren -> wieder Ablagefeld, andere Serie kann hineingezogen werden."""
        self.series = None; self.slice_idx = 0; self.annotations = {}
        self.redraw()
        if self.app.active_panel is self:
            self.app.update_info_panel()

    def fit_to_window(self):
        if not self.series:
            return
        cw, ch = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
        h, w = self.series.vol.shape[1], self.series.vol.shape[2]
        self.zoom = min(cw / w, ch / h) * 0.97
        self.offset = [(cw - w * self.zoom) / 2, (ch - h * self.zoom) / 2]

    def i2c(self, ix, iy): return self.offset[0] + ix * self.zoom, self.offset[1] + iy * self.zoom
    def c2i(self, cx, cy): return (cx - self.offset[0]) / self.zoom, (cy - self.offset[1]) / self.zoom

    def redraw(self):
        self.canvas.delete("all")
        if not self.series:
            self.title.configure(text="—"); self.slice_label.configure(text="")
            self.canvas.create_text(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2,
                                    fill=SUBTLE, font=("", 13), text=self.app.terms.ui("drop_here"))
            return
        pil = self.series.render(self.slice_idx, self.center, self.width, self.invert)
        w = max(int(pil.width * self.zoom), 1); h = max(int(pil.height * self.zoom), 1)
        disp = pil.resize((w, h), Image.NEAREST if self.zoom > 2 else Image.BILINEAR)
        self.tk_image = ImageTk.PhotoImage(disp)
        self.canvas.create_image(self.offset[0], self.offset[1], anchor="nw", image=self.tk_image)
        self.title.configure(text=self.series.desc)
        self.slice_label.configure(text=f"{self.slice_idx + 1}/{self.series.n}")
        self._overlay()
        for a in self.annotations.get(self.slice_idx, []):
            self._render_annotation(a)

    def _overlay(self):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.create_text(8, 8, anchor="nw", fill="#67e8f9", font=("Consolas", 10),
                                text=f"WC {self.center:.0f}  WW {self.width:.0f}  {self.zoom:.2f}x")
        if self.series.edges:
            e = self.series.edges
            self.canvas.create_text(cw / 2, 4, anchor="n", fill=GOOD, font=("", 11, "bold"), text=e["top"])
            self.canvas.create_text(cw / 2, ch - 4, anchor="s", fill=GOOD, font=("", 11, "bold"), text=e["bottom"])
            self.canvas.create_text(4, ch / 2, anchor="w", fill=GOOD, font=("", 11, "bold"), text=e["left"])
            self.canvas.create_text(cw - 4, ch / 2, anchor="e", fill=GOOD, font=("", 11, "bold"), text=e["right"])

    def _render_annotation(self, a):
        (x0, y0), (x1, y1) = [self.i2c(x, y) for (x, y) in a["points"]]
        col = "#fde047"
        if a["type"] == "length":
            self.canvas.create_line(x0, y0, x1, y1, fill=col, width=2)
            self.canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2 - 10, fill=col, font=("", 11, "bold"), text=a["label"])
        elif a["type"] == "rect":
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=col, width=2)
            self.canvas.create_text(min(x0, x1), min(y0, y1) - 8, anchor="sw", fill=col, font=("Consolas", 9), text=a["label"])
        elif a["type"] == "ellipse":
            self.canvas.create_oval(x0, y0, x1, y1, outline=col, width=2)
            self.canvas.create_text(min(x0, x1), min(y0, y1) - 8, anchor="sw", fill=col, font=("Consolas", 9), text=a["label"])
        elif a["type"] == "arrow":
            self.canvas.create_line(x0, y0, x1, y1, fill="#f472b6", width=2, arrow=tk.LAST, arrowshape=(14, 16, 5))

    def _finish(self, tool, p0, p1):
        i0, i1 = self.c2i(*p0), self.c2i(*p1)
        label = ""
        if tool == "length":
            label = self._len_label(i0, i1)
        elif tool in ("rect", "ellipse"):
            label = self._roi(tool, i0, i1)
        self.annotations.setdefault(self.slice_idx, []).append({"type": tool, "points": [i0, i1], "label": label})
        self.redraw()

    def _len_label(self, i0, i1):
        dx, dy = i1[0] - i0[0], i1[1] - i0[1]
        if self.series.pixel_spacing:
            sy, sx = self.series.pixel_spacing
            return f"{np.hypot(dx * sx, dy * sy):.1f} mm"
        return f"{np.hypot(dx, dy):.0f} px"

    def _roi(self, tool, i0, i1):
        x0, x1 = sorted((int(i0[0]), int(i1[0]))); y0, y1 = sorted((int(i0[1]), int(i1[1])))
        h, w = self.series.vol.shape[1], self.series.vol.shape[2]
        x0, x1 = max(0, x0), min(w, x1); y0, y1 = max(0, y0), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            return ""
        data = self.series.modality_slice(self.slice_idx)[y0:y1, x0:x1]
        if self.series.is_color:
            data = data.mean(axis=-1)
        if tool == "ellipse":
            yy, xx = np.mgrid[0:data.shape[0], 0:data.shape[1]]
            cy, cx = data.shape[0] / 2, data.shape[1] / 2
            m = ((xx - cx) / max(cx, 1))**2 + ((yy - cy) / max(cy, 1))**2 <= 1
            data = data[m]
        unit = "HU" if self.series.modality == "CT" else ""
        return f"\u00d8 {data.mean():.0f}{unit}  SD {data.std():.0f}  {data.min():.0f}..{data.max():.0f}"

    def clear_annotations(self):
        self.annotations.pop(self.slice_idx, None); self.redraw()

    def _l_down(self, e):
        self.app.set_active_panel(self); self._drag_start = (e.x, e.y); self._pan_ref = list(self.offset)

    def _l_drag(self, e):
        if not self.series or not self._drag_start:
            return
        tool = self.app.active_tool
        if tool == "pan":
            self.offset[0] = self._pan_ref[0] + (e.x - self._drag_start[0])
            self.offset[1] = self._pan_ref[1] + (e.y - self._drag_start[1])
            self.redraw()
        elif tool == "wl":
            self._wl(e)
        else:
            for it in self._temp:
                self.canvas.delete(it)
            self._temp = []
            x0, y0 = self._drag_start
            col = "#f472b6" if tool == "arrow" else "#fde047"
            if tool == "ellipse":
                self._temp.append(self.canvas.create_oval(x0, y0, e.x, e.y, outline=col, width=2))
            elif tool == "rect":
                self._temp.append(self.canvas.create_rectangle(x0, y0, e.x, e.y, outline=col, width=2))
            else:
                arr = tk.LAST if tool == "arrow" else None
                self._temp.append(self.canvas.create_line(x0, y0, e.x, e.y, fill=col, width=2, arrow=arr))

    def _l_up(self, e):
        if not self.series or not self._drag_start:
            return
        for it in self._temp:
            self.canvas.delete(it)
        self._temp = []
        tool = self.app.active_tool
        if tool in ("length", "rect", "ellipse", "arrow"):
            p0, p1 = self._drag_start, (e.x, e.y)
            if abs(p1[0] - p0[0]) + abs(p1[1] - p0[1]) > 3:
                self._finish(tool, p0, p1)
        self._drag_start = None

    def _r_down(self, e):
        self.app.set_active_panel(self); self._drag_start = (e.x, e.y); self._wl_ref = (self.center, self.width)

    def _r_drag(self, e): self._wl(e)

    def _wl(self, e):
        if not self.series or not self._drag_start:
            return
        rc, rw = getattr(self, "_wl_ref", (self.center, self.width))
        sens = max(self.series.mod_max - self.series.mod_min, 1) / 400.0
        self.width = max(1.0, rw + (e.x - self._drag_start[0]) * sens)
        self.center = rc - (e.y - self._drag_start[1]) * sens
        self.redraw(); self.app.sync_controls_from_active()

    def _motion(self, e):
        if not self.series:
            return
        ix, iy = self.c2i(e.x, e.y)
        h, w = self.series.vol.shape[1], self.series.vol.shape[2]
        if 0 <= ix < w and 0 <= iy < h:
            v = self.series.modality_slice(self.slice_idx)[int(iy), int(ix)]
            if self.series.is_color:
                v = v.mean()
            unit = " HU" if self.series.modality == "CT" else ""
            self.app.set_status(f"Position ({int(ix)}, {int(iy)})   Wert {v:.0f}{unit}")

    def _wheel(self, e):
        if not self.series:
            return
        up = (getattr(e, "num", 0) == 4) or (getattr(e, "delta", 0) > 0)
        if e.state & 0x0004:
            f = 1.1 if up else 1 / 1.1
            ix, iy = self.c2i(e.x, e.y)
            self.zoom = max(0.05, min(self.zoom * f, 20))
            self.offset = [e.x - ix * self.zoom, e.y - iy * self.zoom]
            self.redraw()
        else:
            self.set_slice(self.slice_idx + (-1 if up else 1))

    def set_slice(self, idx):
        if not self.series:
            return
        self.slice_idx = int(np.clip(idx, 0, self.series.n - 1))
        self.redraw(); self.app.update_info_panel()


# ===========================================================================
#  Metadaten-Editor
# ===========================================================================
class MetadataEditor(ctk.CTkToplevel):
    def __init__(self, app, series):
        super().__init__(app.root)
        self.app, self.series = app, series
        self.title(f"Metadaten – {series.desc}")
        self.geometry("840x640"); self.configure(fg_color=PANEL_BG)

        top = ctk.CTkFrame(self, fg_color="transparent"); top.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(top, text="Suche:").pack(side="left", padx=(0, 6))
        self.search = ctk.CTkEntry(top, width=260); self.search.pack(side="left")
        self.search.bind("<KeyRelease>", lambda e: self._fill(self.search.get()))
        ctk.CTkButton(top, text="◀ Schicht", width=80, fg_color=SUBTLE,
                      command=lambda: self._slice(-1)).pack(side="left", padx=(16, 2))
        self.slbl = ctk.CTkLabel(top, text=""); self.slbl.pack(side="left", padx=4)
        ctk.CTkButton(top, text="Schicht ▶", width=80, fg_color=SUBTLE,
                      command=lambda: self._slice(1)).pack(side="left", padx=2)

        style = ttk.Style(); style.theme_use("default")
        style.configure("M.Treeview", background="#0f172a", foreground="#e2e8f0",
                        fieldbackground="#0f172a", rowheight=24, borderwidth=0)
        style.configure("M.Treeview.Heading", background=SUBTLE, foreground="#e2e8f0")
        style.map("M.Treeview", background=[("selected", ACCENT)])
        cols = ("tag", "name", "vr", "value")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", style="M.Treeview")
        for c, t, w in (("tag", "Tag", 120), ("name", "Beschreibung", 250), ("vr", "VR", 45), ("value", "Wert", 350)):
            self.tree.heading(c, text=t); self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=4)
        self.tree.bind("<Double-1>", self._edit)

        # Aktionsleiste 1: Bearbeiten
        act = ctk.CTkFrame(self, fg_color="transparent"); act.pack(fill="x", padx=10, pady=(6, 0))
        self.apply_all = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(act, text="Auf ganze Serie anwenden", variable=self.apply_all,
                        onvalue=True, offvalue=False).pack(side="left")
        ctk.CTkButton(act, text="Tag hinzufügen", width=110, fg_color=SUBTLE,
                      command=self._add_tag).pack(side="right", padx=4)
        ctk.CTkButton(act, text="Tag löschen", width=100, fg_color=SUBTLE,
                      hover_color="#7f1d1d", command=self._del_tag).pack(side="right", padx=4)

        # Aktionsleiste 2: Speichern / Anonymisieren
        bot = ctk.CTkFrame(self, fg_color="transparent"); bot.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(bot, text="Doppelklick auf eine Zeile zum Bearbeiten", text_color=TEXT_MUTED).pack(side="left")
        ctk.CTkButton(bot, text="Anonymisieren…", fg_color="#7c3aed", hover_color="#6d28d9",
                      command=self._anon).pack(side="right", padx=4)
        ctk.CTkButton(bot, text="Serie exportieren…", fg_color=SUBTLE,
                      command=self._export_series).pack(side="right", padx=4)
        ctk.CTkButton(bot, text="Diese Schicht speichern…", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._save).pack(side="right", padx=4)
        self.cur = 0
        self._fill("")

    @property
    def ds(self): return self.series.dsets[self.cur]

    def _targets(self):
        """Datasets, auf die Aenderungen wirken: ganze Serie oder nur aktuelle Schicht."""
        return _unique_datasets(self.series) if self.apply_all.get() else [self.ds]

    def _slice(self, s):
        self.cur = int(np.clip(self.cur + s, 0, self.series.n - 1)); self._fill(self.search.get())

    def _fill(self, filt):
        self.slbl.configure(text=f"Schicht {self.cur + 1}/{self.series.n}")
        self.tree.delete(*self.tree.get_children())
        filt = (filt or "").lower()
        for el in self.ds:
            if el.keyword == "PixelData":
                continue
            val = f"<Sequenz: {len(el.value)}>" if el.VR == "SQ" else str(el.value)
            name = el.name or el.keyword or ""
            tag = f"{el.tag.group:04X},{el.tag.element:04X}"
            if filt and filt not in name.lower() and filt not in val.lower() and filt not in tag.lower():
                continue
            self.tree.insert("", "end", iid=tag, values=(f"({tag})", name, el.VR, val[:200]))

    def _edit(self, _):
        iid = self.tree.focus()
        if not iid:
            return
        g, e_ = [int(x, 16) for x in iid.split(",")]
        tag = Tag(g, e_)
        elem = self.ds[tag]
        if elem.VR == "SQ":
            messagebox.showinfo("Hinweis", "Sequenzen sind hier nicht editierbar."); return
        dlg = ctk.CTkInputDialog(text=f"{elem.name} ({elem.VR})\nNeuer Wert (mehrere mit \\ trennen):",
                                 title="Wert bearbeiten")
        new = dlg.get_input()
        if new is None:
            return
        value = new.split("\\") if "\\" in new else new
        errs = 0
        for d in self._targets():
            if tag in d:
                try:
                    d[tag].value = value
                except Exception:
                    errs += 1
        self._fill(self.search.get())
        self.app.refresh_patient_info()
        scope = "ganze Serie" if self.apply_all.get() else "diese Schicht"
        self.app.set_status(f"Tag {iid} geaendert ({scope})" + (f", {errs} Fehler" if errs else "") + ".")

    def _add_tag(self):
        dlg = ctk.CTkInputDialog(
            text="Neues Tag – Keyword (z. B. PatientComments) ODER Gruppe,Element (z. B. 0010,4000):",
            title="Tag hinzufügen")
        key = dlg.get_input()
        if not key:
            return
        # Tag + VR bestimmen
        try:
            if "," in key:
                g, e_ = [int(x, 16) for x in key.split(",")]
                tag = Tag(g, e_)
                kw = pydicom.datadict.keyword_for_tag(tag)
                vr = pydicom.datadict.dictionary_VR(tag)
            else:
                tag = pydicom.datadict.tag_for_keyword(key)
                if tag is None:
                    messagebox.showerror("Fehler", f"Unbekanntes Keyword: {key}"); return
                tag = Tag(tag)
                kw = key
                vr = pydicom.datadict.dictionary_VR(tag)
        except Exception as ex:
            messagebox.showerror("Fehler", f"Ungueltiges Tag: {ex}"); return
        vdlg = ctk.CTkInputDialog(text=f"Wert fuer {kw or key} ({vr}):", title="Wert")
        val = vdlg.get_input()
        if val is None:
            return
        value = val.split("\\") if "\\" in val else val
        for d in self._targets():
            try:
                d.add_new(tag, vr, value)
            except Exception as ex:
                LOG.warning("add_new fehlgeschlagen: %s", ex)
        self._fill(self.search.get()); self.app.set_status(f"Tag {kw or key} hinzugefuegt.")

    def _del_tag(self):
        iid = self.tree.focus()
        if not iid:
            messagebox.showinfo("Hinweis", "Bitte zuerst eine Zeile auswaehlen."); return
        g, e_ = [int(x, 16) for x in iid.split(",")]
        tag = Tag(g, e_)
        if not messagebox.askyesno("Loeschen", f"Tag ({iid}) wirklich entfernen?"):
            return
        for d in self._targets():
            if tag in d:
                try:
                    del d[tag]
                except Exception:
                    pass
        self._fill(self.search.get()); self.app.set_status(f"Tag {iid} geloescht.")

    def _anon(self):
        AnonymizeDialog(self.app, self.app.series_list, self.series)

    def _export_series(self):
        out = filedialog.askdirectory(title="Zielordner fuer die Serie")
        if not out:
            return
        try:
            n = export_series(self.series, out)
            messagebox.showinfo("Exportiert", f"{n} Datei(en) gespeichert in:\n{out}")
        except Exception as ex:
            messagebox.showerror("Fehler", str(ex))

    def _save(self):
        path = filedialog.asksaveasfilename(defaultextension=".dcm", filetypes=[("DICOM", "*.dcm")])
        if not path:
            return
        try:
            _save_ds(self.ds, path)
            messagebox.showinfo("Gespeichert", path)
        except Exception as ex:
            messagebox.showerror("Fehler", str(ex))


# ===========================================================================
#  Anonymisierungs-Dialog
# ===========================================================================
class AnonymizeDialog(ctk.CTkToplevel):
    def __init__(self, app, series_list, current_series):
        super().__init__(app.root)
        self.app, self.series_list, self.current = app, series_list, current_series
        self.title("Anonymisieren"); self.geometry("520x560"); self.configure(fg_color=PANEL_BG)

        ctk.CTkLabel(self, text="Daten anonymisieren", font=("", 16, "bold"),
                     text_color="#67e8f9").pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(self, text="Entfernt Patienten-, Arzt- und Einrichtungsdaten nach dem "
                                "DICOM-Basisprofil. Hinweis: In das Bild eingebrannter Text "
                                "(burned-in) kann so nicht entfernt werden.",
                     wraplength=480, justify="left", text_color=TEXT_MUTED).pack(anchor="w", padx=16)

        form = ctk.CTkFrame(self, fg_color="transparent"); form.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(form, text="Ersatz-Name:").grid(row=0, column=0, sticky="w", pady=3)
        self.e_name = ctk.CTkEntry(form, width=240); self.e_name.insert(0, "ANONYMOUS")
        self.e_name.grid(row=0, column=1, padx=8)
        ctk.CTkLabel(form, text="Ersatz-ID:").grid(row=1, column=0, sticky="w", pady=3)
        self.e_id = ctk.CTkEntry(form, width=240); self.e_id.insert(0, "ANON")
        self.e_id.grid(row=1, column=1, padx=8)

        self.v_phi = tk.BooleanVar(value=True)
        self.v_dates = tk.BooleanVar(value=True)
        self.v_keep_age = tk.BooleanVar(value=False)
        self.v_priv = tk.BooleanVar(value=True)
        self.v_uid = tk.BooleanVar(value=True)
        self.v_scope_cur = tk.BooleanVar(value=False)
        self.v_update = tk.BooleanVar(value=False)
        opts = [
            (self.v_phi, "Persoenliche Daten entfernen (Namen, Adresse, Aerzte, Klinik, Accession …)"),
            (self.v_dates, "Datumsangaben entfernen (Aufnahme-, Studien-, Geburtsdatum …)"),
            (self.v_keep_age, "Alter & Geschlecht behalten (fuer Forschung)"),
            (self.v_priv, "Private Hersteller-Tags entfernen"),
            (self.v_uid, "UIDs neu generieren (nicht rueckverfolgbar)"),
            (self.v_scope_cur, f"Nur diese Serie ({current_series.desc}) – sonst alle geladenen"),
            (self.v_update, "Auch die geladene Ansicht anonymisieren"),
        ]
        box = ctk.CTkFrame(self, fg_color="transparent"); box.pack(fill="x", padx=16)
        for var, label in opts:
            ctk.CTkCheckBox(box, text=label, variable=var, onvalue=True, offvalue=False,
                            wraplength=440).pack(anchor="w", pady=4)

        btns = ctk.CTkFrame(self, fg_color="transparent"); btns.pack(fill="x", padx=16, pady=16, side="bottom")
        ctk.CTkButton(btns, text="Abbrechen", fg_color=SUBTLE, command=self.destroy).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Anonymisieren & exportieren…", fg_color="#7c3aed",
                      hover_color="#6d28d9", command=self._run).pack(side="right", padx=4)

    def _run(self):
        out = filedialog.askdirectory(title="Zielordner fuer anonymisierte Dateien")
        if not out:
            return
        opts = dict(replace_identity=True,
                    name=self.e_name.get().strip(), id=self.e_id.get().strip(),
                    remove_phi=self.v_phi.get(), remove_dates=self.v_dates.get(),
                    keep_age_sex=self.v_keep_age.get(), remove_private=self.v_priv.get(),
                    regen_uids=self.v_uid.get())
        targets = [self.current] if self.v_scope_cur.get() else self.series_list
        update = self.v_update.get()
        try:
            n = anonymize_and_export(targets, opts, out, update_loaded=update)
        except Exception as ex:
            LOG.error("Anonymisierung fehlgeschlagen: %s", ex)
            messagebox.showerror("Fehler", f"{ex}\n\nDetails im Protokoll:\n{LOG_PATH}"); return
        if update:
            self.app.refresh_patient_info()
            self.app._refresh_thumbnails()
            self.app.update_info_panel()
        messagebox.showinfo("Fertig", f"{n} Datei(en) anonymisiert und gespeichert in:\n{out}")
        self.destroy()


# ===========================================================================
#  Glossar
# ===========================================================================
class GlossaryWindow(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.title("Hilfe / Erklaerungen"); self.geometry("560x620"); self.configure(fg_color=PANEL_BG)
        frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(frame, text="Ebenen / Blickrichtungen", font=("", 15, "bold"),
                     text_color="#67e8f9").pack(anchor="w", pady=(0, 4))
        for p in ("axial", "coronal", "sagittal"):
            ctk.CTkLabel(frame, text=app.terms.plane_label(p), font=("", 13, "bold")).pack(anchor="w", pady=(8, 0))
            ctk.CTkLabel(frame, text=app.terms.plane_explain(p), wraplength=500, justify="left",
                         text_color=TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(frame, text="Begriffe", font=("", 15, "bold"),
                     text_color="#67e8f9").pack(anchor="w", pady=(16, 4))
        for term, text in app.terms.glossary_items():
            ctk.CTkLabel(frame, text=term, font=("", 13, "bold")).pack(anchor="w", pady=(8, 0))
            ctk.CTkLabel(frame, text=text, wraplength=500, justify="left", text_color=TEXT_MUTED).pack(anchor="w")


# ===========================================================================
#  Hauptanwendung
# ===========================================================================
class DicomViewerApp:
    def __init__(self):
        LOG.info("=== PycomViewer Start ===")
        LOG.info("pydicom %s | Decoder verfuegbar: %s | Log: %s",
                 pydicom.__version__, ", ".join(available_decoders()) or "keine", LOG_PATH)
        ctk.set_appearance_mode("dark")
        self.terms = Terms()
        self.root = ctk.CTk()
        self.root.title("PycomViewer – DICOM / MRT Viewer")
        self.root.geometry("1480x900"); self.root.configure(fg_color="#070b10")
        self.root.report_callback_exception = self._report_exc

        self.series_list, self.panels = [], []
        self.active_panel = None
        self.active_tool = "pan"
        self.rows, self.cols = 1, 1
        self.cine_running = False
        self._drag_series = self._ghost = None
        self._retrans = []
        self.patient_name = ctk.StringVar(value="–")
        self.study_date = ctk.StringVar(value="–")

        self._build_menubar()
        self._build_topbar(); self._build_toolbar(); self._build_body(); self._build_statusbar()
        self._rebuild_grid()
        self.root.bind("<Left>", lambda e: self._step(-1))
        self.root.bind("<Right>", lambda e: self._step(1))
        if not has_compressed_decoder():
            LOG.warning("Kein Kompressions-Decoder gefunden – komprimierte DICOMs koennen nicht geladen werden.")
            self.root.after(300, lambda: self.set_status(
                "Hinweis: Kein Decoder fuer komprimierte DICOMs. "
                "Bei Ladefehlern:  pip install python-gdcm"))

    def _report_exc(self, exc, val, tb):
        """Alle unbehandelten Tk-Callback-Fehler ins Log schreiben statt still zu sterben."""
        msg = "".join(traceback.format_exception(exc, val, tb))
        LOG.error("Unbehandelte Ausnahme:\n%s", msg)
        try:
            messagebox.showerror("Fehler", f"{val}\n\nDetails im Protokoll:\n{LOG_PATH}")
        except Exception:
            pass

    # ---- Menueleiste ----------------------------------------------------
    def _build_menubar(self):
        menubar = tk.Menu(self.root)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Bild öffnen…", command=self.load_file)
        m_file.add_command(label="Untersuchung / Ordner öffnen…", command=self.load_folder)
        m_file.add_separator()
        m_file.add_command(label="Aktuelles Bild exportieren…", command=self.export_image)
        m_file.add_separator()
        m_file.add_command(label="Beenden", command=self.root.destroy)
        menubar.add_cascade(label="Datei", menu=m_file)

        m_edit = tk.Menu(menubar, tearoff=0)
        m_edit.add_command(label="Metadaten ansehen / bearbeiten…", command=self.open_metadata)
        m_edit.add_command(label="Anonymisieren…", command=self.open_anonymize)
        menubar.add_cascade(label="Bearbeiten", menu=m_edit)

        m_view = tk.Menu(menubar, tearoff=0)
        m_view.add_command(label="An Fenster anpassen (Fit)", command=self._fit)
        m_view.add_command(label="Invertieren", command=self._invert)
        m_view.add_command(label="Markierungen löschen", command=self._clear)
        m_view.add_command(label="Serien-Liste leeren", command=self.clear_all_series)
        menubar.add_cascade(label="Ansicht", menu=m_view)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="Glossar / Erklärungen…", command=lambda: GlossaryWindow(self))
        menubar.add_cascade(label="Hilfe", menu=m_help)

        # CTk.configure akzeptiert 'menu' je nach Version nicht -> robust setzen
        try:
            self.root.configure(menu=menubar)
        except Exception:
            try:
                tk.Tk.configure(self.root, menu=menubar)
            except Exception:
                self.root.tk.call(self.root._w, "configure", "-menu", str(menubar))

    # ---- Kopfzeile ------------------------------------------------------
    def _build_topbar(self):
        bar = ctk.CTkFrame(self.root, fg_color=PANEL_BG, corner_radius=0); bar.pack(fill="x")
        ctk.CTkLabel(bar, text="  PycomViewer", font=("", 18, "bold"), text_color="#67e8f9").pack(side="left", padx=(6, 18))
        info = ctk.CTkFrame(bar, fg_color="transparent"); info.pack(side="left")
        ctk.CTkLabel(info, text="Patient:", text_color=TEXT_MUTED).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(info, textvariable=self.patient_name).grid(row=0, column=1, sticky="w", padx=(4, 14))
        ctk.CTkLabel(info, text="Datum:", text_color=TEXT_MUTED).grid(row=1, column=0, sticky="w")
        ctk.CTkLabel(info, textvariable=self.study_date).grid(row=1, column=1, sticky="w", padx=(4, 14))

        self.btn_file = ctk.CTkButton(bar, text="", fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.load_file)
        self.btn_file.pack(side="left", padx=6, pady=8)
        self.btn_folder = ctk.CTkButton(bar, text="", fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.load_folder)
        self.btn_folder.pack(side="left", padx=6)
        self._retrans += [(self.btn_file, "load_file"), (self.btn_folder, "load_folder")]

        ctk.CTkLabel(bar, text="Sprache:", text_color=TEXT_MUTED).pack(side="left", padx=(18, 4))
        modes = self.terms.data.get("modes", EMBEDDED_TERMS["modes"])
        self._mode_labels = {modes[m]: m for m in MODE_ORDER}
        self.mode_sel = ctk.CTkSegmentedButton(bar, values=[modes[m] for m in MODE_ORDER], command=self._set_mode,
                                               selected_color=ACCENT, selected_hover_color=ACCENT_HOVER)
        self.mode_sel.set(modes["simple"]); self.mode_sel.pack(side="left", padx=4)

        self.btn_help = ctk.CTkButton(bar, text="", width=140, fg_color=SUBTLE, command=lambda: GlossaryWindow(self))
        self.btn_help.pack(side="right", padx=8); self._retrans.append((self.btn_help, "help"))

    # ---- Werkzeugleiste -------------------------------------------------
    def _build_toolbar(self):
        bar = ctk.CTkFrame(self.root, fg_color="#0a0e13", corner_radius=0); bar.pack(fill="x")
        self.tool_frame = bar
        # Linke Aktions-Buttons zuerst packen, damit der Werkzeug-Wähler mit
        # before=btn_clear davor eingefuegt werden kann (auch beim Sprachwechsel).
        self.btn_clear = ctk.CTkButton(bar, text="Löschen", width=70, fg_color=SUBTLE, command=self._clear)
        self.btn_clear.pack(side="left", padx=4)
        self.btn_invert = ctk.CTkButton(bar, text="Invert", width=70, fg_color=SUBTLE, command=self._invert)
        self.btn_invert.pack(side="left", padx=4)
        self.btn_fit = ctk.CTkButton(bar, text="Fit", width=55, fg_color=SUBTLE, command=self._fit)
        self.btn_fit.pack(side="left", padx=4)
        self.tool_sel = None
        self._build_tool_selector()

        self.lbl_preset = ctk.CTkLabel(bar, text="", text_color=TEXT_MUTED); self.lbl_preset.pack(side="left", padx=(16, 4))
        self.preset_sel = ctk.CTkOptionMenu(bar, values=[self.terms.preset_label(k) for k in WINDOW_PRESETS],
                                            command=self._preset, fg_color=SUBTLE, button_color=ACCENT,
                                            button_hover_color=ACCENT_HOVER, width=140)
        self.preset_sel.pack(side="left", padx=4)
        self._preset_map = {self.terms.preset_label(k): k for k in WINDOW_PRESETS}
        self._retrans.append((self.lbl_preset, "preset"))

        wl = ctk.CTkFrame(bar, fg_color="transparent"); wl.pack(side="left", padx=16)
        ctk.CTkLabel(wl, text="Center", text_color=TEXT_MUTED).grid(row=0, column=0, padx=4)
        self.center_slider = ctk.CTkSlider(wl, from_=0, to=1, width=130, command=self._center,
                                           button_color=ACCENT, progress_color=ACCENT); self.center_slider.grid(row=0, column=1)
        ctk.CTkLabel(wl, text="Width", text_color=TEXT_MUTED).grid(row=1, column=0, padx=4)
        self.width_slider = ctk.CTkSlider(wl, from_=1, to=2, width=130, command=self._width,
                                          button_color=ACCENT, progress_color=ACCENT); self.width_slider.grid(row=1, column=1)

        self.cine_btn = ctk.CTkButton(bar, text="▶", width=90, fg_color=SUBTLE, command=self._cine); self.cine_btn.pack(side="right", padx=8)
        self.btn_export = ctk.CTkButton(bar, text="", fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.export_image)
        self.btn_export.pack(side="right", padx=4)
        self.btn_meta = ctk.CTkButton(bar, text="", fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.open_metadata)
        self.btn_meta.pack(side="right", padx=4)
        self.btn_anon = ctk.CTkButton(bar, text="Anonymisieren…", fg_color="#7c3aed",
                                      hover_color="#6d28d9", command=self.open_anonymize)
        self.btn_anon.pack(side="right", padx=4)
        self._retrans += [(self.btn_export, "export"), (self.btn_meta, "metadata")]

    def _build_tool_selector(self):
        if self.tool_sel is not None:
            self.tool_sel.destroy()
        self._tool_map = {self.terms.tool_label(t): t for t in TOOL_IDS}
        self.tool_sel = ctk.CTkSegmentedButton(self.tool_frame, values=[self.terms.tool_label(t) for t in TOOL_IDS],
                                               command=self._set_tool, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER)
        self.tool_sel.set(self.terms.tool_label(self.active_tool))
        self.tool_sel.pack(side="left", padx=8, pady=8, before=self.btn_clear)

    # ---- Sidebar + Viewport --------------------------------------------
    def _build_body(self):
        body = ctk.CTkFrame(self.root, fg_color="transparent"); body.pack(fill="both", expand=True)
        side = ctk.CTkFrame(body, fg_color=SIDEBAR_BG, corner_radius=0, width=250)
        side.pack(side="left", fill="y"); side.pack_propagate(False)

        shead = ctk.CTkFrame(side, fg_color="transparent")
        shead.pack(fill="x", padx=10, pady=(10, 4))
        self.lbl_series = ctk.CTkLabel(shead, text="", font=("", 13, "bold"), wraplength=180, justify="left")
        self.lbl_series.pack(side="left"); self._retrans.append((self.lbl_series, "series_title"))
        ctk.CTkButton(shead, text="Alle ×", width=56, height=24, fg_color=SUBTLE,
                      hover_color="#7f1d1d", command=self.clear_all_series).pack(side="right")
        self.series_box = ctk.CTkScrollableFrame(side, fg_color="transparent", height=340)
        self.series_box.pack(fill="both", expand=True, padx=6)
        self.empty_hint = ctk.CTkLabel(self.series_box, text="", text_color=TEXT_MUTED, wraplength=210, justify="left")
        self.empty_hint.pack(pady=20)

        self.lbl_view = ctk.CTkLabel(side, text="", font=("", 13, "bold"))
        self.lbl_view.pack(fill="x", padx=10, pady=(8, 2)); self._retrans.append((self.lbl_view, "view_title"))
        vf = ctk.CTkFrame(side, fg_color="transparent"); vf.pack(fill="x", padx=8)
        self.view_btns = {}
        for i, key in enumerate(("front", "side", "top")):
            b = ctk.CTkButton(vf, text="", width=70, fg_color=SUBTLE, command=lambda k=key: self._choose_view(k))
            b.grid(row=0, column=i, padx=3, pady=2); self.view_btns[key] = b

        self.lbl_info = ctk.CTkLabel(side, text="", font=("", 13, "bold"))
        self.lbl_info.pack(fill="x", padx=10, pady=(12, 2)); self._retrans.append((self.lbl_info, "info_title"))
        self.info_text = ctk.CTkLabel(side, text="—", wraplength=228, justify="left", text_color=TEXT_MUTED, anchor="w")
        self.info_text.pack(fill="x", padx=10, pady=(0, 10))

        right = ctk.CTkFrame(body, fg_color="transparent"); right.pack(side="left", fill="both", expand=True)
        ctrl = ctk.CTkFrame(right, fg_color="transparent"); ctrl.pack(fill="x", padx=8, pady=(8, 0))
        self.lbl_layout = ctk.CTkLabel(ctrl, text="", text_color=TEXT_MUTED); self.lbl_layout.pack(side="left")
        self._retrans.append((self.lbl_layout, "layout"))
        self._stepper(ctrl, "rows"); self._stepper(ctrl, "cols")
        for spec, lbl in (((1, 1), "1×1"), ((1, 2), "1×2"), ((2, 2), "2×2"), ((1, 3), "1×3")):
            ctk.CTkButton(ctrl, text=lbl, width=45, fg_color=SUBTLE, command=lambda s=spec: self._set_grid(*s)).pack(side="left", padx=2)

        self.viewport = ctk.CTkFrame(right, fg_color="#070b10"); self.viewport.pack(fill="both", expand=True, padx=6, pady=6)

    def _stepper(self, parent, which):
        f = ctk.CTkFrame(parent, fg_color="transparent"); f.pack(side="left", padx=(12, 4))
        lbl = ctk.CTkLabel(f, text="", text_color=TEXT_MUTED); lbl.grid(row=0, column=0, padx=2)
        self._retrans.append((lbl, which))
        ctk.CTkButton(f, text="−", width=28, fg_color=SUBTLE, command=lambda: self._bump(which, -1)).grid(row=0, column=1)
        val = ctk.CTkLabel(f, text="1", width=20); val.grid(row=0, column=2, padx=2)
        ctk.CTkButton(f, text="+", width=28, fg_color=SUBTLE, command=lambda: self._bump(which, +1)).grid(row=0, column=3)
        setattr(self, f"_{which}_val", val)

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self.root, fg_color=PANEL_BG, corner_radius=0, height=26); bar.pack(fill="x")
        self.status = ctk.CTkLabel(bar, text="Bereit.", text_color=TEXT_MUTED, anchor="w"); self.status.pack(side="left", padx=10)

    # ---- Sprache --------------------------------------------------------
    def _set_mode(self, label):
        self.terms.mode = self._mode_labels.get(label, "simple"); self._retranslate()

    def _retranslate(self):
        for widget, key in self._retrans:
            try:
                widget.configure(text=self.terms.ui(key))
            except Exception:
                pass
        for key, b in self.view_btns.items():
            b.configure(text=self.terms.view_label(key))
        self._build_tool_selector()
        self.preset_sel.configure(values=[self.terms.preset_label(k) for k in WINDOW_PRESETS])
        self._preset_map = {self.terms.preset_label(k): k for k in WINDOW_PRESETS}
        self._refresh_thumbnails(); self.update_info_panel()

    # ---- Drag & Drop ----------------------------------------------------
    def begin_drag(self, series, e):
        self._drag_series = series
        pil = series.thumbnail(90)
        self._ghost = tk.Toplevel(self.root); self._ghost.overrideredirect(True); self._ghost.attributes("-topmost", True)
        self._ghost_img = ImageTk.PhotoImage(pil)
        tk.Label(self._ghost, image=self._ghost_img, text=series.desc, compound="top",
                 bg=ACCENT, fg="white", font=("", 10), padx=4, pady=4).pack()
        self._move_ghost(e)

    def update_drag(self, e):
        if not self._ghost:
            return
        self._move_ghost(e)
        target = self._panel_from(self.root.winfo_containing(e.x_root, e.y_root))
        for p in self.panels:
            if p is target:
                p.configure(border_width=2, border_color=GOOD)
            elif p is self.active_panel:
                p.configure(border_width=2, border_color=ACCENT)
            else:
                p.configure(border_width=0)

    def end_drag(self, e):
        if self._ghost:
            self._ghost.destroy(); self._ghost = None
        target = self._panel_from(self.root.winfo_containing(e.x_root, e.y_root))
        if target and self._drag_series:
            target.assign_series(self._drag_series)
        self._drag_series = None
        self.set_active_panel(self.active_panel or (self.panels[0] if self.panels else None))

    def _move_ghost(self, e): self._ghost.geometry(f"+{e.x_root + 12}+{e.y_root + 12}")

    def _panel_from(self, w):
        for _ in range(8):
            if isinstance(w, ImagePanel):
                return w
            w = getattr(w, "master", None)
            if w is None:
                break
        return None

    # ---- Layout ---------------------------------------------------------
    def _bump(self, which, d):
        setattr(self, which, int(np.clip(getattr(self, which) + d, 1, 4))); self._rebuild_grid()

    def _set_grid(self, rows, cols):
        self.rows, self.cols = rows, cols; self._rebuild_grid()

    def _rebuild_grid(self):
        old = [(p.series, p.slice_idx) for p in self.panels]
        for p in self.panels:
            p.destroy()
        self.panels = []
        for r in range(4):
            self.viewport.grid_rowconfigure(r, weight=1 if r < self.rows else 0)
        for c in range(4):
            self.viewport.grid_columnconfigure(c, weight=1 if c < self.cols else 0)
        idx = 0
        for r in range(self.rows):
            for c in range(self.cols):
                panel = ImagePanel(self.viewport, self)
                panel.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
                self.panels.append(panel)
                if idx < len(old) and old[idx][0] is not None:
                    s = old[idx][0]
                    panel.series = s; panel.center = s.default_center; panel.width = s.default_width
                    panel.slice_idx = old[idx][1]
                    panel.after(60, panel.fit_to_window); panel.after(70, panel.redraw)
                idx += 1
        self._rows_val.configure(text=str(self.rows)); self._cols_val.configure(text=str(self.cols))
        if self.panels:
            self.set_active_panel(self.panels[0])

    # ---- Laden ----------------------------------------------------------
    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[
            ("DICOM / IMA / DICOMDIR", "*.dcm *.ima *.IMA *.img DICOMDIR"),
            ("Alle Dateien", "*.*")])
        if not path:
            return
        errors = []
        if os.path.basename(path).upper() == "DICOMDIR":
            items = load_items_from_dicomdir(path)
            if not items:
                messagebox.showerror("Fehler", "DICOMDIR enthaelt keine lesbaren Bilder."); return
            self._add(build_series_from_datasets(items, errors),
                      os.path.basename(os.path.dirname(path)), "DICOMDIR", errors)
            return
        ds = _read_dataset(path)
        if ds is None:
            messagebox.showerror("Fehler", "Keine gueltige DICOM-/IMA-Bilddatei."); return
        self._add(build_series_from_datasets([(path, ds)], errors),
                  os.path.basename(path), "Datei", errors)

    def load_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.set_status("Lade … (Ordner wird durchsucht)"); self.root.update_idletasks()
        errors = []
        items, how = load_items_from_folder(folder)
        if not items:
            messagebox.showerror(
                "Nichts gefunden",
                "Keine DICOM-/IMA-Bilder gefunden (auch kein DICOMDIR).\n\n"
                f"Details siehe Logdatei:\n{LOG_PATH}")
            return
        self._add(build_series_from_datasets(items, errors), os.path.basename(folder), how, errors)

    def _diagnose(self, errors):
        """Aus den gesammelten Fehlern eine verstaendliche Meldung bauen."""
        compressed = any(any(ts in e or name in e for ts, name in COMPRESSED_TS.items())
                         for e in errors)
        head = "Es konnte keine Serie erstellt werden.\n\n"
        if compressed:
            head += DECODER_HINT + "\n\nVorhandene Decoder: " + \
                    (", ".join(available_decoders()) or "keine") + "\n\n"
        detail = "\n".join(f"• {e}" for e in errors[:6])
        return head + (detail if detail else "Keine dekodierbaren Bilder gefunden.") + \
            f"\n\nVollstaendiges Protokoll:\n{LOG_PATH}"

    def _add(self, new, name, how="Datei", errors=None):
        if not new:
            LOG.error("Keine Serie erstellt. Fehler: %s", errors)
            messagebox.showerror("Serie konnte nicht erstellt werden",
                                 self._diagnose(errors or []))
            return
        self.series_list.extend(new); self._refresh_thumbnails()
        target = next((p for p in self.panels if p.series is None), None) or (self.panels[0] if self.panels else None)
        if target:
            target.assign_series(new[0])
        self.refresh_patient_info()
        note = ""
        if errors:
            note = f"  ({len(errors)} Bild(er) uebersprungen – siehe Log)"
        self.set_status(f"Geladen ({how}): {name} – {len(new)} Serie(n), "
                        f"{sum(s.n for s in new)} Schicht(en).{note}")

    def remove_series(self, series):
        """Serie aus der Liste links entfernen und aus allen Panels loesen."""
        if series in self.series_list:
            self.series_list.remove(series)
        for p in self.panels:
            if p.series is series:
                p.clear_panel()
        self._refresh_thumbnails(); self.update_info_panel()
        self.set_status(f"Serie entfernt: {series.desc}")

    def clear_all_series(self):
        self.series_list = []
        for p in self.panels:
            p.clear_panel()
        self._refresh_thumbnails(); self.update_info_panel()
        self.set_status("Alle Serien entfernt.")

    def _refresh_thumbnails(self):
        for w in self.series_box.winfo_children():
            w.destroy()
        if not self.series_list:
            ctk.CTkLabel(self.series_box, text=self.terms.ui("no_series"), text_color=TEXT_MUTED,
                         wraplength=210, justify="left").pack(pady=20)
            return
        for s in self.series_list:
            ThumbnailWidget(self.series_box, self, s).pack(fill="x", pady=4)

    def refresh_patient_info(self):
        if self.active_panel and self.active_panel.series:
            ds = self.active_panel.series.dsets[0]
            self.patient_name.set(str(ds.get("PatientName", "–")))
            sd = str(ds.get("StudyDate", "–"))
            if len(sd) == 8:
                sd = f"{sd[6:8]}.{sd[4:6]}.{sd[0:4]}"
            self.study_date.set(sd)

    # ---- Blickrichtung / Info ------------------------------------------
    def _choose_view(self, key):
        plane = self.terms.view_plane(key)
        match = next((s for s in self.series_list if s.plane == plane), None)
        if not match:
            avail = sorted({self.terms.plane_label(s.plane) for s in self.series_list})
            self.set_status("Keine passende Serie. Vorhanden: " + (", ".join(avail) if avail else "keine")); return
        target = self.active_panel or (self.panels[0] if self.panels else None)
        if target:
            target.assign_series(match)
        self.set_status(f"{self.terms.plane_label(plane)}: {self.terms.plane_explain(plane)}")

    def update_info_panel(self):
        p = self.active_panel
        if not p or not p.series:
            self.info_text.configure(text="—"); return
        s = p.series
        lines = [s.desc, f"{s.modality} · {s.n} Schicht(en)", self.terms.plane_label(s.plane)]
        if s.edges:
            e = s.edges
            lines.append(f"↑ {self.terms.direction(e['top'])}   ↓ {self.terms.direction(e['bottom'])}")
            lines.append(f"← {self.terms.direction(e['left'])}   → {self.terms.direction(e['right'])}")
        expl = self.terms.plane_explain(s.plane)
        if expl:
            lines += ["", expl]
        self.info_text.configure(text="\n".join(lines))

    # ---- aktives Panel / Slider ----------------------------------------
    def set_active_panel(self, panel):
        if panel is None:
            return
        for p in self.panels:
            p.configure(border_width=0)
        panel.configure(border_width=2, border_color=ACCENT)
        self.active_panel = panel
        self.refresh_patient_info(); self.sync_controls_from_active(); self.update_info_panel()

    def sync_controls_from_active(self):
        p = self.active_panel
        if not p or not p.series:
            return
        lo, hi = p.series.mod_min, p.series.mod_max; span = max(hi - lo, 1)
        self.center_slider.configure(from_=lo, to=hi); self.width_slider.configure(from_=1, to=span)
        self._syncing = True
        self.center_slider.set(float(np.clip(p.center, lo, hi)))
        self.width_slider.set(float(np.clip(p.width, 1, span)))
        self._syncing = False

    def _center(self, v):
        if getattr(self, "_syncing", False):
            return
        if self.active_panel and self.active_panel.series:
            self.active_panel.center = float(v); self.active_panel.redraw()

    def _width(self, v):
        if getattr(self, "_syncing", False):
            return
        if self.active_panel and self.active_panel.series:
            self.active_panel.width = float(v); self.active_panel.redraw()

    # ---- Aktionen -------------------------------------------------------
    def _set_tool(self, label):
        self.active_tool = self._tool_map.get(label, "pan")
        cur = {"pan": "fleur", "wl": "sb_h_double_arrow", "probe": "crosshair"}.get(self.active_tool, "tcross")
        for p in self.panels:
            p.canvas.configure(cursor=cur)
        self.set_status(self.terms.tool_tip(self.active_tool) or "")

    def _preset(self, label):
        p = self.active_panel
        if not p or not p.series:
            return
        preset = WINDOW_PRESETS[self._preset_map.get(label, "auto")]
        if preset is None:
            p.center, p.width = p.series.auto_window(p.slice_idx)
        else:
            p.center, p.width = preset
        p.redraw(); self.sync_controls_from_active()

    def _invert(self):
        if self.active_panel and self.active_panel.series:
            self.active_panel.invert = not self.active_panel.invert; self.active_panel.redraw()

    def _fit(self):
        if self.active_panel and self.active_panel.series:
            self.active_panel.fit_to_window(); self.active_panel.redraw()

    def _clear(self):
        if self.active_panel:
            self.active_panel.clear_annotations()

    def _step(self, s):
        if self.active_panel:
            self.active_panel.set_slice(self.active_panel.slice_idx + s)

    def open_metadata(self):
        if self.active_panel and self.active_panel.series:
            MetadataEditor(self, self.active_panel.series)
        else:
            messagebox.showinfo("Hinweis", "Bitte zuerst eine Serie laden.")

    def open_anonymize(self):
        if not self.series_list:
            messagebox.showinfo("Hinweis", "Bitte zuerst eine Serie laden."); return
        current = self.active_panel.series if (self.active_panel and self.active_panel.series) \
            else self.series_list[0]
        AnonymizeDialog(self, self.series_list, current)

    def export_image(self):
        p = self.active_panel
        if not p or not p.series:
            messagebox.showinfo("Hinweis", "Kein Bild zum Exportieren."); return
        path = filedialog.asksaveasfilename(defaultextension=".jpg", filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")])
        if not path:
            return
        img = p.series.render(p.slice_idx, p.center, p.width, p.invert)
        if path.lower().endswith((".jpg", ".jpeg")):
            img = img.convert("RGB")
        try:
            img.save(path); messagebox.showinfo("Erfolg", path)
        except Exception as ex:
            messagebox.showerror("Fehler", str(ex))

    def _cine(self):
        self.cine_running = not self.cine_running
        self.cine_btn.configure(text="⏸" if self.cine_running else "▶",
                                fg_color=ACCENT if self.cine_running else SUBTLE)
        if self.cine_running:
            self._cine_step()

    def _cine_step(self):
        if not self.cine_running:
            return
        p = self.active_panel
        if p and p.series and p.series.n > 1:
            p.set_slice((p.slice_idx + 1) % p.series.n)
        self.root.after(120, self._cine_step)

    def set_status(self, text):
        self.status.configure(text=text)

    def run(self):
        self._retranslate(); self.root.mainloop()


if __name__ == "__main__":
    DicomViewerApp().run()