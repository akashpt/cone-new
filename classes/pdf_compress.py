import subprocess
import os
import time


def compress_pdf(input_pdf, output_pdf, quality="screen"):
    """
    Compress PDF using Ghostscript.

    quality options:
    - screen   (~72 DPI)   -> BEST for <5MB
    - ebook    (~150 DPI)
    - printer  (~300 DPI)
    """

    if not os.path.exists(input_pdf):
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")

    start = time.time()

    command = [
        "gs",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS=/{quality}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={output_pdf}",
        input_pdf
    ]

    subprocess.run(command, check=True)

    elapsed = time.time() - start
    size_mb = os.path.getsize(output_pdf) / (1024 * 1024)

    print(f"[PDF] Compression done in {elapsed:.2f}s | Size={size_mb:.2f} MB")

    return output_pdf
