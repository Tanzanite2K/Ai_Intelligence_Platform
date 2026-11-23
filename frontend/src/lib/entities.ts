/** Organisation IDs mirror the API: "e_" + base64url(UTF-8 canonical name), no padding (src/api/v1/entity_ids.py). */
export function encodeEntityId(name: string): string {
  const bytes = new TextEncoder().encode(name);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return `e_${btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}`;
}

export const entityHref = (nameOrId: string, isId = false) => `/organisations/${isId ? nameOrId : encodeEntityId(nameOrId)}`;

export const METHOD_INFO: Record<string, { label: string; help: string }> = {
  EXACT: { label: "Exact", help: "Normalised name matched an existing organisation exactly." },
  ALIAS: { label: "Alias", help: "Matched a curated alias or website domain of an existing organisation." },
  FUZZY: { label: "Fuzzy", help: "High-similarity name match that passed the guards (score stored as confidence)." },
  NEW: { label: "New", help: "No existing organisation matched; registered as a new organisation." },
  REVIEW: { label: "Review", help: "Ambiguous match kept separate until a human decides (stored confidence 0.5)." },
};
