import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <div
      className="flex min-h-screen items-center justify-center"
      style={{ background: "var(--bg-tertiary)" }}
    >
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1
            className="text-[20px] font-medium tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            STRØM Import
          </h1>
          <p className="mt-2 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            Log ind for at importere produkter
          </p>
        </div>
        <SignIn
          appearance={{
            elements: {
              rootBox: "mx-auto",
              card: "shadow-md rounded-xl",
            },
          }}
        />
      </div>
    </div>
  );
}
