import type { ExplorerConfig } from "./explorer";

const NEWEST_OLDEST_NAME = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "name", label: "Name A–Z" },
];

export const CONFIGS: Record<"startups" | "products" | "papers" | "jobs" | "news", ExplorerConfig> = {
  startups: {
    slug: "startups",
    title: "Startups",
    description: "AI startups from Y Combinator and Hugging Face organisations.",
    searchPlaceholder: "Search startups by name, description, tag or location…",
    filters: ["source", "category", "location"],
    labels: { category: { label: "Tag", placeholder: "e.g. Computer Vision", hint: "Matches the startup's source tags" } },
    sorts: [{ value: "name", label: "Name A–Z" }, { value: "newest", label: "Recently collected" }, { value: "oldest", label: "First collected" }],
    grid: true,
    note: "Industry, founding date and logo are not provided by these sources, so they are not shown. Startups carry no publication date; date sorting uses when the platform collected them.",
  },
  products: {
    slug: "products",
    title: "Products",
    description: "AI models and applications from the Hugging Face Hub.",
    searchPlaceholder: "Search products by name, maker or task…",
    filters: ["source", "category", "organisation", "pricing_model"],
    labels: {
      category: { label: "Task", placeholder: "e.g. text-generation", hint: "Hugging Face pipeline task (exact match)" },
      organisation: { label: "Maker / organisation", placeholder: "e.g. Qwen" },
    },
    sorts: [{ value: "name", label: "Name A–Z" }, { value: "newest", label: "Recently collected" }, { value: "oldest", label: "First collected" }],
    note: "Pricing: FREE is derived from an open-weight licence tag in the Hub metadata (the licence is shown as evidence). Other pricing values appear only when extracted from text with a verbatim evidence quote; none are stored yet. Products without a licence tag have no pricing.",
  },
  papers: {
    slug: "papers",
    title: "Research papers",
    description: "Recent AI papers with live GitHub repository stars.",
    searchPlaceholder: "Search papers by title, abstract, author or arXiv ID…",
    filters: ["source", "min_stars", "date_from", "fresh_hours"],
    labels: { date_from: { label: "Published from" } },
    sorts: [{ value: "newest", label: "Newest first" }, { value: "oldest", label: "Oldest first" }, { value: "stars", label: "Most GitHub stars" }, { value: "name", label: "Title A–Z" }],
    note: "Stars are live GitHub API counts; papers without a linked repository show “no repository”. Research categories and organisation links are not stored.",
  },
  jobs: {
    slug: "jobs",
    title: "Jobs",
    description: "AI job postings collected inside the 24-hour freshness window.",
    searchPlaceholder: "Search jobs by title, company, description or location…",
    filters: ["source", "organisation", "location", "category", "remote", "fresh_hours", "date_from"],
    labels: {
      organisation: { label: "Company", placeholder: "e.g. Patronus AI" },
      category: { label: "Role family", placeholder: "e.g. Engineering", hint: "Exact match" },
      date_from: { label: "Posted from" },
    },
    sorts: NEWEST_OLDEST_NAME,
    note: "Salary, employment type and experience requirements are not stored by the job sources, so they are not shown.",
  },
  news: {
    slug: "news",
    title: "News",
    description: "AI news articles collected inside the 24-hour freshness window.",
    searchPlaceholder: "Search headlines and summaries…",
    filters: ["source", "organisation", "tagged", "date_from", "fresh_hours", "verified_dates"],
    labels: {
      source: { label: "Publisher" },
      organisation: { label: "Organisation tag", placeholder: "e.g. Anthropic", hint: "Exact tag name, case-insensitive; searches validated tags only, not article text" },
      date_from: { label: "Published from" },
    },
    sorts: NEWEST_OLDEST_NAME,
    note: "Organisation tags are extracted from the article text and stored only when each name is backed by a verbatim quote from the article.",
  },
};
