import { useSyncExternalStore } from "react";
import hi from "../../extension/locales/hi.json";
type Language = "en" | "hi";
let language: Language =
  localStorage.getItem("veil.language") === "hi" ? "hi" : "en";
const listeners = new Set<() => void>();
export function getLanguage() {
  return language;
}
export function setLanguage(value: Language) {
  language = value;
  localStorage.setItem("veil.language", value);
  document.documentElement.lang = value;
  for (const listener of listeners) listener();
}
export function useLocale() {
  return useSyncExternalStore((callback) => {
    listeners.add(callback);
    return () => {
      listeners.delete(callback);
    };
  }, getLanguage);
}
export function t(text: string): string {
  if (language !== "hi") return text;
  const key = text.trim(),
    translated = (hi as Record<string, string>)[key];
  return translated ? text.replace(key, translated) : text;
}
export function LanguageSelector() {
  const value = useLocale();
  return (
    <label className="language-selector">
      <span>{t("Language")}</span>
      <select
        aria-label="Language / भाषा"
        value={value}
        onChange={(event) => setLanguage(event.target.value as Language)}
      >
        <option value="en">English</option>
        <option value="hi">हिन्दी</option>
      </select>
    </label>
  );
}
document.documentElement.lang = language;
