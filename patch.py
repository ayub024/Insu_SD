import re

with open("c:/Users/Ayub/Desktop/finance_UW/syntheticV3/core/growth_engine.py", "r") as f:
    code = f.read()

# 1. Capture target_scale
code = re.sub(
    r'target_scale = active_preset\.get\("target_scale", scenario\.get\("target_scale", \{\}\)\)',
    r'self._target_scale = active_preset.get("target_scale", scenario.get("target_scale", {}))\n        target_scale = self._target_scale',
    code,
    count=1
)

# 2. Add ratio_key
code = re.sub(
    r'self\._broker_targets = self\._build_staff_targets\(self\._growth\.get\("brokers", \{\}\)\)',
    r'self._broker_targets = self._build_staff_targets(self._growth.get("brokers", {}), "min_policy_to_broker_ratio")',
    code,
    count=1
)

code = re.sub(
    r'self\._underwriter_targets = self\._build_staff_targets\(self\._growth\.get\("underwriters", \{\}\)\)',
    r'self._underwriter_targets = self._build_staff_targets(self._growth.get("underwriters", {}), "min_policy_to_underwriter_ratio")',
    code,
    count=1
)

# 3. Modify _build_staff_targets definition
old_def = r'''    def _build_staff_targets(self, cfg: dict) -> dict[str, int]:
        start_count = int(cfg.get("start_count", 0))
        end_count = int(cfg.get("end_count", start_count))
        total_new = max(0, end_count - start_count)'''

new_def = r'''    def _build_staff_targets(self, cfg: dict, ratio_key: str = "") -> dict[str, int]:
        start_count = int(cfg.get("start_count", 0))
        end_count = int(cfg.get("end_count", start_count))

        ratio = float(self._target_scale.get(ratio_key, 0)) if hasattr(self, '_target_scale') else 0.0
        if ratio > 0 and getattr(self, '_policy_total', 0) > 0:
            expected_end = max(1, int(self._policy_total / ratio))
            if end_count > 0:
                scale_factor = expected_end / float(end_count)
                start_count = int(start_count * scale_factor)
                end_count = expected_end

        total_new = max(0, end_count - start_count)'''

code = code.replace(old_def, new_def)

with open("c:/Users/Ayub/Desktop/finance_UW/syntheticV3/core/growth_engine.py", "w") as f:
    f.write(code)
