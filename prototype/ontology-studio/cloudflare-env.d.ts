declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    BUCKET?: R2Bucket;
    KNOWLEDGEOS_API_BASE_URL?: string;
    KNOWLEDGEOS_API_TOKEN?: string;
  }
}
