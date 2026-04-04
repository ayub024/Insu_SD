import re

with open("c:/Users/Ayub/Desktop/finance_UW/syntheticV3/master_data/broker_master.py", "r") as f:
    code = f.read()

target = r'''        self.start_count = int(brokers_cfg.get("start_count", 140))
        self.end_count = int(brokers_cfg.get("end_count", 620))'''

replacement = r'''        self.start_count = int(brokers_cfg.get("start_count", 140))
        self.end_count = int(brokers_cfg.get("end_count", 620))

        scenario_payload = _load_yaml("config/scenario.yaml").get("scenario", {})
        
        mode = str(scenario_payload.get("mode", "")).strip().lower()
        if mode not in {"dev", "prod"}:
            if scenario_payload.get("dev_mode"): mode = "dev"
            elif scenario_payload.get("prod_mode"): mode = "prod"
            else: mode = "dev"
            
        active_preset = scenario_payload.get("presets", {}).get(mode, {}) or {}
        target_scale = active_preset.get("target_scale", scenario_payload.get("target_scale", {}))
        
        ratio = float(target_scale.get("min_policy_to_broker_ratio", 0))
        policy_total = int(target_scale.get("total_policies", 0))

        if ratio > 0 and policy_total > 0:
            expected_end = max(1, int(policy_total / ratio))
            if self.end_count > 0:
                scale_factor = expected_end / float(self.end_count)
                self.start_count = int(self.start_count * scale_factor)
                self.end_count = expected_end'''

code = code.replace(target, replacement)

with open("c:/Users/Ayub/Desktop/finance_UW/syntheticV3/master_data/broker_master.py", "w") as f:
    f.write(code)
