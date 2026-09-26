"use client";

// Nothing below this renders until there is a Supabase session. The backend
// refuses unauthenticated requests anyway; this only stops every screen from
// loading into a wall of 401s.

import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "@/lib/supabase";

export default function AuthGate({ children }: { children: ReactNode }) {
  // undefined = still asking Supabase; null = signed out.
  const [session, setSession] = useState<Session | null | undefined>(undefined);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => data.subscription.unsubscribe();
  }, []);

  if (session === undefined) return null;
  if (session === null) return <SignIn />;
  return <>{children}</>;
}

function SignIn() {
  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "error" | "info"; text: string } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    const { data, error } =
      mode === "in"
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({ email, password });
    setBusy(false);
    if (error?.code === "email_address_invalid") {
      // Supabase checks that an address can actually receive mail, not just
      // that it looks like one, so a made-up address is refused here.
      setMessage({
        tone: "error",
        text: `${error.message}. Sign-up needs a real address you can receive email at — a confirmation link is sent there.`,
      });
    } else if (error) {
      setMessage({ tone: "error", text: error.message });
    } else if (mode === "up" && !data.session) {
      // Supabase's "Confirm email" setting is on: no session until the link is clicked.
      setMessage({ tone: "info", text: `Check ${email} for a confirmation link, then sign in.` });
      setMode("in");
    }
    // On success onAuthStateChange in AuthGate swaps this screen out.
  }

  const input =
    "w-full rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-[14px] text-zinc-100 outline-none placeholder:text-zinc-500 focus:border-zinc-600";

  return (
    <main className="flex h-full w-full items-center justify-center p-4">
      <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-3">
        <div className="mb-2 flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded border border-zinc-700/80 bg-zinc-900 font-mono text-xs font-semibold tracking-wider text-zinc-100">
            CP
          </div>
          <h1 className="text-lg font-medium text-zinc-100">
            {mode === "in" ? "Sign in to CIPHER" : "Create a CIPHER account"}
          </h1>
        </div>

        <label className="flex flex-col gap-1 text-[12px] text-zinc-400">
          Email
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={input}
          />
        </label>
        <label className="flex flex-col gap-1 text-[12px] text-zinc-400">
          Password
          <input
            type="password"
            required
            minLength={6}
            autoComplete={mode === "in" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={input}
          />
        </label>

        {message && (
          <p
            role={message.tone === "error" ? "alert" : "status"}
            className={`text-[13px] ${message.tone === "error" ? "text-red-400" : "text-zinc-300"}`}
          >
            {message.text}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="mt-1 rounded bg-indigo-600 px-3 py-2 text-[14px] font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy ? "…" : mode === "in" ? "Sign in" : "Create account"}
        </button>
        <button
          type="button"
          onClick={() => {
            setMode(mode === "in" ? "up" : "in");
            setMessage(null);
          }}
          className="text-[13px] text-zinc-400 hover:text-zinc-200"
        >
          {mode === "in" ? "No account? Create one" : "Have an account? Sign in"}
        </button>
      </form>
    </main>
  );
}
