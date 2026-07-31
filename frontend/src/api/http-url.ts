export function safeHttpUrl(value: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    const hasHttpProtocol = url.protocol === "http:" || url.protocol === "https:";
    return hasHttpProtocol && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}
