import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KnowledgeOS Ontology Studio",
  description: "KnowledgeOS 本体可视化、影响分析与 ChangeSet 治理交互原型。",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
