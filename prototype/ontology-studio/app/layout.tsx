import type { Metadata } from "next";
import "./globals.css";
import { KnowledgeWorkspace } from "./workspace-shell";

export const metadata: Metadata = {
  title: "KnowledgeOS 4.0 · 结构化知识工作台",
  description: "在独立工作区中建模本体、搜索实例与编排专家流程模板，追踪影响并通过 ChangeSet 治理变更。",
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
      <body className="antialiased"><KnowledgeWorkspace>{children}</KnowledgeWorkspace></body>
    </html>
  );
}
