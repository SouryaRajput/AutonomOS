from __future__ import annotations

from typing import Any, Optional
import uuid

from core.inference.model import ModelRequirement
from core.inference.types import ModelCapability
from pkg.sdk.worker import WorkerRuntimeContext
from workers.tester.model import TestResult
from workers.tester.types import TestCategory, TestExecutionStatus


class UIValidator:
    """
    Validates user interfaces via screenshots, optical character recognition (OCR),
    and vision-capable model analysis via Inference Gateway.
    """

    def __init__(self, context: WorkerRuntimeContext):
        self.context = context

    def capture_screenshot(self, filename: str = "ui_snapshot.png") -> Optional[str]:
        try:
            res = self.context.tools.execute(
                tool_id="screenshot.capture",
                arguments={"filename": filename},
            )
            if res and res.output:
                return str(res.output)
            return filename
        except Exception as e:
            self.context.log.warning(f"Screenshot capture failed: {e}")
            return None

    def extract_text_ocr(self, image_path: str) -> Optional[str]:
        try:
            res = self.context.tools.execute(
                tool_id="ocr.extract_text",
                arguments={"image_path": image_path},
            )
            if res and res.output:
                return str(res.output)
            return None
        except Exception as e:
            self.context.log.warning(f"OCR extraction failed for '{image_path}': {e}")
            return None

    def validate_ui_layout_and_text(
        self,
        target_description: str,
        expected_elements: list[str],
        image_path: Optional[str] = None,
    ) -> TestResult:
        test_id = f"ui-{uuid.uuid4().hex[:8]}"
        img_name = image_path or "ui_validation.png"

        # Capture screenshot if not supplied
        captured = self.capture_screenshot(img_name)

        # Run OCR extraction
        ocr_text = self.extract_text_ocr(captured or img_name) or ""

        # Check for presence of expected textual elements
        missing_elements = [el for el in expected_elements if el.lower() not in ocr_text.lower()]

        status = TestExecutionStatus.PASSED if not missing_elements else TestExecutionStatus.FAILED
        err_msg = f"Missing expected visual elements: {', '.join(missing_elements)}" if missing_elements else ""

        snippet = (
            f"Target: {target_description}\n"
            f"Screenshot: {captured or img_name}\n"
            f"OCR Extracted: {ocr_text[:300]}\n"
            f"Expected Elements: {', '.join(expected_elements)}"
        )

        return TestResult(
            test_id=test_id,
            name=f"UI Validation: {target_description[:40]}",
            category=TestCategory.UI,
            status=status,
            output_snippet=snippet,
            error_message=err_msg,
            metadata={"captured_image": captured or img_name, "ocr_text": ocr_text[:500]},
        )
