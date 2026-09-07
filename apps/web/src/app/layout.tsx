import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import NavRail from "@/components/NavRail";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "CIPHER",
  description: "Multi-persona AI assistant — JARVIS, FRIDAY, ULTRON",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // `suppressHydrationWarning` here is about extensions, not about this app.
    //
    // Browser extensions run before React hydrates and a number of them stamp
    // their own attributes on <html> -- a `hydrated` class, a theme attribute,
    // a scanner's marker. React compares the server's HTML against the DOM it
    // finds, sees an attribute the server never sent, and reports a mismatch
    // that no change to this file could fix.
    //
    // Narrow enough to be safe: it suppresses mismatches for THIS element's
    // own attributes only, and never for its children, so a genuine hydration
    // bug anywhere in the tree still reports normally. There is nothing here
    // for it to hide either -- the className below is two font variables and
    // two literals, with no state, no locale and no clock in it.
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      {/* `overflow-hidden` is what makes the shell a desktop app rather than a
          document: every screen scrolls its own panes, and the page itself
          never does. */}
      <body className="h-full overflow-hidden bg-zinc-950 font-sans text-zinc-200">
        <div className="flex h-full w-full overflow-hidden">
          <NavRail />
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
        </div>
      </body>
    </html>
  );
}
