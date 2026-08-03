import Link from "next/link";

/**
 * Landing page. Wordmark plus one link — the scaffold's only job is to prove
 * the UI boots and can reach the engine, so there is nothing else here yet.
 *
 * The wordmark is text, not an image, so it stays selectable, scalable and
 * translatable, and no asset needs licensing.
 */
export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 px-6 py-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-fg-primary">
          Repcut
        </h1>
        <p className="text-sm text-fg-secondary">
          Local-first AI video editor for gym footage. Your footage stays on this
          machine.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Link
          href="/prompts"
          className="w-fit rounded-lg border border-line bg-raised px-4 py-2 text-sm font-medium text-fg-primary transition-colors duration-micro ease-micro hover:border-line-strong"
        >
          Prompt completion dashboard
        </Link>
        <Link
          href="/status"
          className="w-fit rounded-lg border border-line bg-raised px-4 py-2 text-sm text-fg-secondary transition-colors duration-micro ease-micro hover:border-line-strong hover:text-fg-primary"
        >
          Check engine status
        </Link>
      </div>
    </main>
  );
}
