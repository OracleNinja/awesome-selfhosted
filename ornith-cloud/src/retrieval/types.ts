export interface SearchResult {
  title: string;
  url: string;
  snippet: string;
  /** ISO date when the source reports one. */
  published?: string;
}

export interface SearchOptions {
  limit?: number;
  signal?: AbortSignal;
}

export interface SearchProvider {
  readonly name: string;
  search(query: string, options?: SearchOptions): Promise<SearchResult[]>;
}

export interface FetchedDocument {
  url: string;
  status: number;
  contentType: string;
  html: string;
  truncated: boolean;
}

export interface FetchProvider {
  readonly name: string;
  fetch(url: string, signal?: AbortSignal): Promise<FetchedDocument>;
}

/** A source after extraction, ready to be fenced into the prompt. */
export interface RetrievedSource {
  title: string;
  url: string;
  domain: string;
  snippet: string;
  text: string;
  published?: string;
  truncated: boolean;
  /** True when this came from cache rather than a live fetch. */
  fromCache: boolean;
  /** When the underlying data was actually obtained. */
  retrievedAt: string;
}

export interface RetrievalResult {
  sources: RetrievedSource[];
  searchMs: number;
  fetchMs: number;
  /** Non-fatal problems worth surfacing in logs, never to the model. */
  warnings: string[];
}
