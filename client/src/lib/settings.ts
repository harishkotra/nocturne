import type { Health, Settings } from "./types";

export const SETTINGS_KEY = "nocturne.settings.v1";

export const PRESET_ORDER = ["particle", "lmstudio", "ollama", "gemini", "custom"] as const;

export const FALLBACK_PRESETS: Record<string, { label: string; baseUrl: string; model: string; needsKey: string }> = {
  particle: { label: "Particle.ai", baseUrl: "https://api.particle.ai/v1", model: "deepseek-v4.1-flash", needsKey: "true" },
  lmstudio: { label: "LM Studio (local)", baseUrl: "http://localhost:1234/v1", model: "google/gemma-4-e4b", needsKey: "false" },
  ollama: { label: "Ollama (local)", baseUrl: "http://localhost:11434/v1", model: "llama3.2", needsKey: "false" },
  gemini: { label: "Gemini", baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/", model: "gemini-2.5-flash", needsKey: "true" },
  custom: { label: "Custom", baseUrl: "", model: "", needsKey: "true" },
};

export const DEFAULT_SETTINGS: Settings = {
  version: 1,
  provider: { preset: "particle", baseUrl: "https://api.particle.ai/v1", apiKey: "", model: "deepseek-v4.1-flash" },
  policy: { maxActiveFacts: 120, staleAfterDays: 45, protectedCategories: ["identity", "preferences", "commitments"] },
  dryRun: false,
};

export function loadSettings(): Settings {
  if (typeof localStorage === "undefined") return DEFAULT_SETTINGS;
  const raw = localStorage.getItem(SETTINGS_KEY);
  if (!raw) return DEFAULT_SETTINGS;
  try {
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return {
      version: 1,
      provider: { ...DEFAULT_SETTINGS.provider, ...(parsed.provider ?? {}) },
      policy: { ...DEFAULT_SETTINGS.policy, ...(parsed.policy ?? {}) },
      dryRun: Boolean(parsed.dryRun),
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(settings: Settings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    /* storage full or blocked: settings stay in memory for this session */
  }
}

/** First run only: adopt whatever the server is actually configured with. */
export function mergeHealthDefaults(settings: Settings, health: Health): Settings {
  const { baseUrl, model } = health.provider;
  const preset =
    PRESET_ORDER.find((key) => {
      const known = health.presets[key] ?? FALLBACK_PRESETS[key];
      return known?.baseUrl === baseUrl;
    }) ?? "custom";
  return {
    ...settings,
    provider: { preset, baseUrl, model: model || settings.provider.model, apiKey: "" },
    policy: health.defaults.policy,
  };
}
