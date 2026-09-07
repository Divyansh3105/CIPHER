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
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
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
