"""OpenCV 4.x ``FONT_HERSHEY_SIMPLEX`` text, drawn identically under every OpenCV version.

OpenCV 5 replaced the Hershey stroke fonts of ``cv2.putText`` / ``cv2.getTextSize`` by a
TrueType renderer: same call, thinner glyphs, other widths, and the thick dark outline the
app draws under its labels nearly disappears. The detection gym (``tools/det_gym.py``,
``training.real_art._text``, ``render``) draws our overlay labels ("ADC 12 s") over the minimap with it:
under OpenCV 5 the labels became thin and outline-free, i.e. a much easier test than what the
exe (pinned OpenCV 4.10) really draws, and the gym scored ~3 points of recall better for the
same detection code. This module replays the 4.x algorithm (same glyph strokes, same 16-bit
fixed point, one ``cv2.polylines`` per stroke, as ``cv::putText``): bit-identical to
``cv2.putText`` under OpenCV 4.x, and the same pixels under OpenCV 5.

Glyph data: the Hershey simplex glyphs of OpenCV 4.10 (``modules/imgproc/src/hershey_fonts.cpp``,
public-domain Hershey fonts), printable ASCII 32-126.
"""

from __future__ import annotations

import cv2
import numpy as np

XY_SHIFT = 16
_BASE_LINE = 9          # HersheySimplex header (9 + 12 * 16): base line 9, cap line 12
_CAP_LINE = 12

#: glyph strings for chr(32) .. chr(126) (first two chars: left / right bearings)
_GLYPHS = (
    'JZ', 'MWRFRT RYQZR[SZRY', 'JZNFNM VFVM', 'G]OFOb UFUb JQZQ JWZW',
    'H\\PBP_ TBT_ YIWGTFPFMGKIKKLMMNOOUQWRXSYUYXWZT[P[MZKX', 'F^[FYGVHSHPGNFLFJGIIIKKMMMOLPJPHNF [FI[ YTWTUUTWTYV[X[ZZ[X[VYT', 'E_\\O\\N[MZMYNXPVUTXRZP[L[JZIYHWHUISJRQNRMSKSIRGPFNGMIMKNNPQUXWZY[[[\\Z\\Y', 'NVRFRM',
    'KYVBTDRGPKOPOTPYR]T`Vb', 'KYNBPDRGTKUPUTTYR]P`Nb', 'JZRLRX MOWU WOMU', 'E_RIR[ IR[R',
    'MWSZR[QZRYSZS\\R^Q_', 'E_IR[R', 'MWRYQZR[SZRY', 'G][BIb',
    'H\\QFNGLJKOKRLWNZQ[S[VZXWYRYOXJVGSFQF', 'H\\NJPISFS[', 'H\\LKLJMHNGPFTFVGWHXJXLWNUQK[Y[', 'H\\MFXFRNUNWOXPYSYUXXVZS[P[MZLYKW',
    'H\\UFKTZT UFU[', 'H\\WFMFLOMNPMSMVNXPYSYUXXVZS[P[MZLYKW', 'H\\XIWGTFRFOGMJLOLTMXOZR[S[VZXXYUYTXQVOSNRNOOMQLT', 'H\\YFO[ KFYF',
    'H\\PFMGLILKMMONSOVPXRYTYWXYWZT[P[MZLYKWKTLRNPQOUNWMXKXIWGTFPF', 'H\\XMWPURRSQSNRLPKMKLLINGQFRFUGWIXMXRWWUZR[P[MZLX', 'MWRMQNROSNRM RYQZR[SZRY', 'MWRMQNROSNRM SZR[QZRYSZS\\R^Q_',
    'F^ZIJRZ[', 'E_IO[O IU[U', 'F^JIZRJ[', 'I[LKLJMHNGPFTFVGWHXJXLWNVORQRT RYQZR[SZRY',
    'DaWNVLTKQKOLNMMOMRNTOUQVTVVUWS WKWSXUYV[V\\U]S]O\\L[JYHWGTFQFNGLHJJILHOHRIUJWLYNZQ[T[WZYY', 'I[RFJ[ RFZ[ MTWT', 'G\\KFK[ KFTFWGXHYJYLXNWOTP KPTPWQXRYTYWXYWZT[K[', 'H]ZKYIWGUFQFOGMILKKNKSLVMXOZQ[U[WZYXZV',
    'G\\KFK[ KFRFUGWIXKYNYSXVWXUZR[K[', 'H[LFL[ LFYF LPTP L[Y[', 'HZLFL[ LFYF LPTP', 'H]ZKYIWGUFQFOGMILKKNKSLVMXOZQ[U[WZYXZVZS USZS',
    'G]KFK[ YFY[ KPYP', 'NVRFR[', 'JZVFVVUYTZR[P[NZMYLVLT', 'G\\KFK[ YFKT POY[',
    'HYLFL[ L[X[', 'F^JFJ[ JFR[ ZFR[ ZFZ[', 'G]KFK[ KFY[ YFY[', 'G]PFNGLIKKJNJSKVLXNZP[T[VZXXYVZSZNYKXIVGTFPF',
    'G\\KFK[ KFTFWGXHYJYMXOWPTQKQ', 'G]PFNGLIKKJNJSKVLXNZP[T[VZXXYVZSZNYKXIVGTFPF SWY]', 'G\\KFK[ KFTFWGXHYJYLXNWOTPKP RPY[', 'H\\YIWGTFPFMGKIKKLMMNOOUQWRXSYUYXWZT[P[MZKX',
    'JZRFR[ KFYF', 'G]KFKULXNZQ[S[VZXXYUYF', 'I[JFR[ ZFR[', 'F^HFM[ RFM[ RFW[ \\FW[',
    'H\\KFY[ YFK[', 'I[JFRPR[ ZFRP', 'H\\YFK[ KFYF K[Y[', 'KYOBOb OBVB ObVb',
    'G]IL[b', 'KYUBUb NBUB NbUb', 'G]JTROZT JTRPZT', 'I[J[Z[',
    'LXPFUL PFOGUL', 'I\\XMX[ XPVNTMQMONMPLSLUMXOZQ[T[VZXX', 'H[LFL[ LPNNPMSMUNWPXSXUWXUZS[P[NZLX', 'I[XPVNTMQMONMPLSLUMXOZQ[T[VZXX',
    'I\\XFX[ XPVNTMQMONMPLSLUMXOZQ[T[VZXX', 'I[LSXSXQWOVNTMQMONMPLSLUMXOZQ[T[VZXX', 'MYWFUFSGRJR[ OMVM', 'I\\XMX]W`VaTbQbOa XPVNTMQMONMPLSLUMXOZQ[T[VZXX',
    'I\\MFM[ MQPNRMUMWNXQX[', 'NVQFRGSFREQF RMR[', 'MWRFSGTFSERF SMS^RaPbNb', 'IZMFM[ WMMW QSX[',
    'NVRFR[', 'CaGMG[ GQJNLMOMQNRQR[ RQUNWMZM\\N]Q][', 'I\\MMM[ MQPNRMUMWNXQX[', 'I\\QMONMPLSLUMXOZQ[T[VZXXYUYSXPVNTMQM',
    'H[LMLb LPNNPMSMUNWPXSXUWXUZS[P[NZLX', 'I\\XMXb XPVNTMQMONMPLSLUMXOZQ[T[VZXX', 'KXOMO[ OSPPRNTMWM', 'J[XPWNTMQMNNMPNRPSUTWUXWXXWZT[Q[NZMX',
    'MYRFRWSZU[W[ OMVM', 'I\\MMMWNZP[S[UZXW XMX[', 'JZLMR[ XMR[', 'G]JMN[ RMN[ RMV[ ZMV[',
    'J[MMX[ XMM[', 'JZLMR[ XMR[P_NaLbKb', 'J[XMM[ MMXM M[X[', 'KYTBQEPHPJQMSOSPORSTSUQWPZP\\Q_Tb',
    'NVRBRb', 'KYPBSETHTJSMQOQPURQTQUSWTZT\\S_Pb', 'F^IUISJPLONOPPTSVTXTZS[Q ISJQLPNPPQTTVUXUZT[Q[O',
)


def _glyph(ch: str) -> str:
    c = ord(ch)
    if c < 32 or c > 126:
        c = ord("?")          # (cv::putText: out-of-range -> '?')
    return _GLYPHS[c - 32]


def get_text_size(text: str, scale: float, thickness: int = 1) -> tuple[tuple[int, int], int]:
    """``cv2.getTextSize(text, FONT_HERSHEY_SIMPLEX, scale, thickness)`` of OpenCV 4.x."""
    h = int(round((_CAP_LINE + _BASE_LINE) * scale + (thickness + 1) // 2))
    vx = 0.0
    for ch in str(text):
        g = _glyph(ch)
        vx += ((ord(g[1]) - 82) - (ord(g[0]) - 82)) * scale
    return (int(round(vx + thickness)), h), int(round(_BASE_LINE * scale + thickness * 0.5))


def put_text(img: np.ndarray, text: str, org: tuple[int, int], scale: float,
             color: tuple, thickness: int = 1, line_type: int = cv2.LINE_8) -> np.ndarray:
    """``cv2.putText(img, text, org, FONT_HERSHEY_SIMPLEX, scale, color, thickness,
    line_type)`` of OpenCV 4.x, in place (returns ``img``)."""
    if not text:
        return img
    if line_type == cv2.LINE_AA and img.dtype != np.uint8:
        line_type = cv2.LINE_8
    one = 1 << XY_SHIFT
    hscale = vscale = int(round(scale * one))
    view_x = int(org[0]) << XY_SHIFT
    view_y = (int(org[1]) << XY_SHIFT) - _BASE_LINE * vscale
    for ch in str(text):
        g = _glyph(ch)
        left, right = ord(g[0]) - 82, ord(g[1]) - 82
        dx = right * hscale
        view_x -= left * hscale
        for stroke in g[2:].split(" "):
            if len(stroke) < 4:
                continue
            pts = [((ord(stroke[k]) - 82) * hscale + view_x, (ord(stroke[k + 1]) - 82) * vscale + view_y)
                   for k in range(0, len(stroke) - 1, 2)]
            cv2.polylines(img, [np.asarray(pts, np.int32).reshape(-1, 1, 2)], False, color,
                          thickness, line_type, XY_SHIFT)
        view_x += dx
    return img
