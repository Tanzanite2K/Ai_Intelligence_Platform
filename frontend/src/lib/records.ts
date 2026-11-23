import type { RecordType, TypeSlug } from "./api/schemas";

export const SLUG_OF: Record<RecordType, TypeSlug> = {
  STARTUP: "startups",
  PRODUCT: "products",
  RESEARCH_PAPER: "papers",
  JOB: "jobs",
  NEWS: "news",
};

export const TYPE_OF_SLUG: Record<TypeSlug, RecordType> = {
  startups: "STARTUP",
  products: "PRODUCT",
  papers: "RESEARCH_PAPER",
  jobs: "JOB",
  news: "NEWS",
};

export const recordHref = (type: RecordType, id: number) => `/${SLUG_OF[type]}/${id}`;

/** Human labels for stored content keys (detail pages). Unknown keys fall back to a readable form. */
export const FIELD_LABEL: Record<string, string> = {
  entityName: "Name",
  rawName: "Name as written by source",
  data: "Additional data",
  description: "Description",
  website: "Website",
  location: "Location",
  tags: "Tags",
  productName: "Product name",
  startupName: "Maker / organisation",
  rawStartupName: "Maker as written by source",
  pricingModel: "Pricing model",
  title: "Title",
  authors: "Authors",
  paper_url: "Paper",
  github_url: "GitHub repository",
  github_stars: "GitHub stars",
  github_stars_checked_at: "Stars checked at",
  github_stars_source: "Stars source",
  published_date: "Published",
  arxiv_id: "arXiv ID",
  abstract: "Abstract",
  company: "Company",
  raw_company: "Company as written by source",
  date: "Posted",
  date_verified: "Date verified",
  date_source: "Date source",
  is_remote: "Remote",
  role_family: "Role family",
  job_url: "Job posting",
  article_url: "Article",
  author: "Author",
  full_text: "Full text",
  summary: "Summary",
  word_count: "Word count",
  organizations: "Organisations (validated)",
};

export const fieldLabel = (k: string) => FIELD_LABEL[k] ?? k.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase());
