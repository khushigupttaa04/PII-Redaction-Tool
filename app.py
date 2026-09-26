"""Web app: upload a document or ticket log, download the redacted version."""
import os
import tempfile
from collections import Counter

import streamlit as st

from redactor.pipeline import leak_check, redact_file

st.title("PII Redaction Tool")
st.caption("Replaces personal and business-identifying information with consistent fake values, "
           "keeping the document's structure and formatting.")

upload = st.file_uploader("Upload a .docx, .txt, .log or .csv file", type=["docx", "txt", "log", "csv"])
if upload and st.button("Redact"):
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, upload.name), os.path.join(tmp, "redacted_" + upload.name)
        open(src, "wb").write(upload.getbuffer())
        with st.spinner("Detecting and replacing PII... (a 100+ page document takes under a minute)"):
            log = redact_file(src, dst)
        leaks = leak_check(dst, log)
        st.success(f"Replaced {len(log)} PII items")
        if leaks:
            st.warning(f"Leak check: {len(leaks)} replaced values still appear: {leaks}")
        else:
            st.info("Leak check passed: none of the replaced values remain in the output")
        st.table(Counter(item["type"] for item in log).most_common())       # what was found, by type
        st.download_button("Download redacted file", open(dst, "rb").read(), file_name="redacted_" + upload.name)
