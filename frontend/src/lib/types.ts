/**
 * Shared types used across multiple pages.
 * Keep in sync with backend response schemas.
 */

/** Import summary — returned by GET /imports */
export interface ImportSummary {
  id: string;
  name: string;
  is_test: boolean;
  status: string;
  file_name: string;
  file_count: number;
  total_products: number;
  products_pushed: number;
  created_at: string;
}
