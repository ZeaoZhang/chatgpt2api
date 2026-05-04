"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, LoaderCircle, Pencil, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  createPromptTemplate,
  deletePromptTemplate,
  deletePromptTemplates,
  fetchPromptTemplates,
  importPromptTemplatesFromGithub,
  importPromptTemplatesFromMarkdown,
  type PromptTemplate,
  updatePromptTemplate,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type EditorState = {
  title: string;
  category: string;
  tags: string;
  template_text: string;
};

const emptyEditor: EditorState = {
  title: "",
  category: "",
  tags: "",
  template_text: "",
};

function parseTags(value: string) {
  return value
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function editorFromTemplate(item: PromptTemplate): EditorState {
  return {
    title: item.title || "",
    category: item.category || "",
    tags: (item.tags || []).join(", "),
    template_text: item.template_text || "",
  };
}

function PromptTemplatesContent() {
  const [items, setItems] = useState<PromptTemplate[]>([]);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState<PromptTemplate | null>(null);
  const [editor, setEditor] = useState<EditorState>(emptyEditor);
  const [importOpen, setImportOpen] = useState(false);
  const [importMode, setImportMode] = useState<"github" | "markdown">("github");
  const [githubUrl, setGithubUrl] = useState("https://github.com/YouMind-OpenLab/awesome-gpt-image-2");
  const [githubPaths, setGithubPaths] = useState("README.md");
  const [markdownText, setMarkdownText] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<PromptTemplate | null>(null);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const categories = useMemo(() => Array.from(new Set(items.map((item) => item.category).filter(Boolean))), [items]);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const deletableItems = useMemo(() => items.filter((item) => item.scope !== "builtin"), [items]);
  const currentDeletableIds = useMemo(() => deletableItems.map((item) => item.id), [deletableItems]);
  const currentAllSelected = currentDeletableIds.length > 0 && currentDeletableIds.every((id) => selectedSet.has(id));

  const openCreateEditor = () => {
    setEditingTemplate(null);
    setEditor(emptyEditor);
    setEditorOpen(true);
  };

  const openEditEditor = (item: PromptTemplate) => {
    setEditingTemplate(item);
    setEditor(editorFromTemplate(item));
    setEditorOpen(true);
  };

  const closeEditor = (open: boolean) => {
    setEditorOpen(open);
    if (!open) {
      setEditingTemplate(null);
      setEditor(emptyEditor);
    }
  };

  const toggleSelected = (ids: string[], checked: boolean) => {
    setSelectedIds((current) => {
      if (checked) {
        return Array.from(new Set([...current, ...ids]));
      }
      const removeSet = new Set(ids);
      return current.filter((id) => !removeSet.has(id));
    });
  };

  const loadTemplates = async () => {
    setIsLoading(true);
    try {
      const data = await fetchPromptTemplates({ query });
      setItems(data.items);
      setSelectedIds((current) => current.filter((id) => data.items.some((item) => item.id === id && item.scope !== "builtin")));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载模板失败");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadTemplates();
  }, []);

  const handleSaveTemplate = async () => {
    if (!editor.template_text.trim()) {
      toast.error("请输入模板内容");
      return;
    }
    setIsSaving(true);
    try {
      const payload = {
        title: editor.title.trim() || editor.template_text.trim().slice(0, 24),
        category: editor.category.trim(),
        tags: parseTags(editor.tags),
        template_text: editor.template_text,
      };
      if (editingTemplate) {
        const item = await updatePromptTemplate(editingTemplate.id, payload);
        setItems((current) => current.map((value) => (value.id === item.id ? item : value)));
        toast.success("模板已更新");
      } else {
        const item = await createPromptTemplate(payload);
        setItems((current) => [item, ...current]);
        toast.success("模板已保存");
      }
      closeEditor(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存模板失败");
    } finally {
      setIsSaving(false);
    }
  };

  const handleImport = async () => {
    setIsSaving(true);
    try {
      const result =
        importMode === "github"
          ? await importPromptTemplatesFromGithub({
              repo_url: githubUrl,
              paths: githubPaths
                .split(/[\n,，]+/)
                .map((item) => item.trim())
                .filter(Boolean),
              tags: ["github"],
            })
          : await importPromptTemplatesFromMarkdown({
              markdown: markdownText,
              tags: ["markdown"],
            });
      setItems((current) => [...result.items, ...current]);
      setImportOpen(false);
      toast.success(`已导入 ${result.imported} 个模板，跳过 ${result.skipped} 个重复项`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "导入模板失败");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsSaving(true);
    try {
      await deletePromptTemplate(deleteTarget.id);
      setItems((current) => current.filter((item) => item.id !== deleteTarget.id));
      setSelectedIds((current) => current.filter((id) => id !== deleteTarget.id));
      setDeleteTarget(null);
      toast.success("模板已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除模板失败");
    } finally {
      setIsSaving(false);
    }
  };

  const handleBulkDelete = async () => {
    const ids = selectedIds.filter((id) => deletableItems.some((item) => item.id === id));
    if (ids.length === 0) return;
    setIsSaving(true);
    try {
      const result = await deletePromptTemplates(ids);
      const missingSet = new Set(result.missing_ids || []);
      const deletedIds = ids.filter((id) => !missingSet.has(id));
      const deletedSet = new Set(deletedIds);
      setItems((current) => current.filter((item) => !deletedSet.has(item.id)));
      setSelectedIds((current) => current.filter((id) => !deletedSet.has(id)));
      setBulkDeleteOpen(false);
      toast.success(`已删除 ${result.deleted} 个模板`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "批量删除模板失败");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="space-y-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Prompts</div>
          <h1 className="text-2xl font-semibold tracking-tight">模板库</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-stone-400" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void loadTemplates();
              }}
              placeholder="搜索模板"
              className="h-10 w-[220px] rounded-xl border-stone-200 bg-white pl-9"
            />
          </div>
          <Button variant="outline" onClick={() => void loadTemplates()} disabled={isLoading} className="h-10 rounded-xl border-stone-200 bg-white">
            {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            刷新
          </Button>
          <Button variant="outline" onClick={() => setImportOpen(true)} className="h-10 rounded-xl border-stone-200 bg-white">
            <Download className="size-4" />
            导入
          </Button>
          <Button onClick={openCreateEditor} className="h-10 rounded-xl bg-stone-950 text-white hover:bg-stone-800">
            <Plus className="size-4" />
            新建
          </Button>
        </div>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-stone-100 px-5 py-4 text-sm text-stone-500">
            <Checkbox checked={currentAllSelected} onCheckedChange={(checked) => toggleSelected(currentDeletableIds, Boolean(checked))} disabled={currentDeletableIds.length === 0 || isSaving} />
            <span>共 {items.length} 个模板</span>
            {selectedIds.length > 0 ? <span className="text-stone-700">已选 {selectedIds.length} 个</span> : null}
            <Button variant="outline" className="h-8 rounded-lg border-rose-200 bg-white px-3 text-rose-600 hover:bg-rose-50" onClick={() => setBulkDeleteOpen(true)} disabled={selectedIds.length === 0 || isSaving}>
              <Trash2 className="size-4" />
              批量删除
            </Button>
            <button type="button" className="text-sm text-stone-500 hover:text-stone-900 disabled:text-stone-300" onClick={() => setSelectedIds([])} disabled={selectedIds.length === 0 || isSaving}>
              清空选择
            </button>
            {categories.slice(0, 8).map((category) => (
              <button
                key={category}
                type="button"
                onClick={() => {
                  setQuery(category || "");
                }}
                className="rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-600 transition hover:bg-stone-200"
              >
                {category}
              </button>
            ))}
          </div>
          <div className="divide-y divide-stone-100">
            {items.map((item) => (
              <article key={item.id} className="grid gap-3 px-5 py-4 transition hover:bg-stone-50 lg:grid-cols-[auto_minmax(0,1fr)_auto]">
                <div className="pt-1">
                  <Checkbox checked={selectedSet.has(item.id)} onCheckedChange={(checked) => toggleSelected([item.id], Boolean(checked))} disabled={item.scope === "builtin" || isSaving} />
                </div>
                <div className="min-w-0 space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="truncate text-base font-semibold text-stone-950">{item.title}</h2>
                    {item.category ? <span className="rounded-full bg-stone-100 px-2 py-0.5 text-xs text-stone-500">{item.category}</span> : null}
                    {item.scope === "imported" ? <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">导入</span> : null}
                  </div>
                  <p className="line-clamp-3 text-sm leading-6 text-stone-600">{item.template_text}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {(item.variables || []).map((variable) => (
                      <span key={variable.name} className="rounded-full bg-stone-100 px-2 py-0.5 text-xs text-stone-500">
                        {variable.name}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex items-center justify-end gap-2">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-9 rounded-lg text-stone-400 hover:bg-stone-100 hover:text-stone-900"
                    onClick={() => openEditEditor(item)}
                    disabled={item.scope === "builtin"}
                  >
                    <Pencil className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-9 rounded-lg text-stone-400 hover:bg-rose-50 hover:text-rose-600"
                    onClick={() => setDeleteTarget(item)}
                    disabled={item.scope === "builtin"}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </article>
            ))}
          </div>
          {!isLoading && items.length === 0 ? <div className="px-6 py-14 text-center text-sm text-stone-500">还没有模板</div> : null}
        </CardContent>
      </Card>

      <Dialog open={editorOpen} onOpenChange={closeEditor}>
        <DialogContent className="w-[min(92vw,720px)] rounded-2xl">
          <DialogHeader>
            <DialogTitle>{editingTemplate ? "编辑模板" : "新建模板"}</DialogTitle>
            <DialogDescription>使用双大括号定义变量，例如 {"{{subject}}"}。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <Input value={editor.title} onChange={(event) => setEditor((current) => ({ ...current, title: event.target.value }))} placeholder="标题" className="rounded-xl" />
            <div className="grid gap-3 sm:grid-cols-2">
              <Input value={editor.category} onChange={(event) => setEditor((current) => ({ ...current, category: event.target.value }))} placeholder="分类" className="rounded-xl" />
              <Input value={editor.tags} onChange={(event) => setEditor((current) => ({ ...current, tags: event.target.value }))} placeholder="标签，逗号分隔" className="rounded-xl" />
            </div>
            <Textarea value={editor.template_text} onChange={(event) => setEditor((current) => ({ ...current, template_text: event.target.value }))} placeholder="模板内容" className="min-h-[240px] rounded-xl" />
          </div>
          <DialogFooter>
            <Button variant="outline" className="rounded-xl" onClick={() => closeEditor(false)} disabled={isSaving}>
              取消
            </Button>
            <Button className="rounded-xl bg-stone-950 text-white hover:bg-stone-800" onClick={() => void handleSaveTemplate()} disabled={isSaving}>
              {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent className="w-[min(92vw,760px)] rounded-2xl">
          <DialogHeader>
            <DialogTitle>导入模板</DialogTitle>
            <DialogDescription>支持 GitHub README/cases Markdown，也可以直接粘贴 Markdown。</DialogDescription>
          </DialogHeader>
          <div className="flex gap-2">
            <Button variant={importMode === "github" ? "default" : "outline"} className="rounded-xl" onClick={() => setImportMode("github")}>
              GitHub
            </Button>
            <Button variant={importMode === "markdown" ? "default" : "outline"} className="rounded-xl" onClick={() => setImportMode("markdown")}>
              Markdown
            </Button>
          </div>
          {importMode === "github" ? (
            <div className="grid gap-3">
              <Input value={githubUrl} onChange={(event) => setGithubUrl(event.target.value)} placeholder="GitHub 仓库或 README 地址" className="rounded-xl" />
              <Textarea value={githubPaths} onChange={(event) => setGithubPaths(event.target.value)} placeholder="路径，每行一个，例如 README.md 或 cases/poster.md" className="min-h-[96px] rounded-xl" />
            </div>
          ) : (
            <Textarea value={markdownText} onChange={(event) => setMarkdownText(event.target.value)} placeholder="粘贴 Markdown 内容" className="min-h-[280px] rounded-xl" />
          )}
          <DialogFooter>
            <Button variant="outline" className="rounded-xl" onClick={() => setImportOpen(false)} disabled={isSaving}>
              取消
            </Button>
            <Button className="rounded-xl bg-stone-950 text-white hover:bg-stone-800" onClick={() => void handleImport()} disabled={isSaving}>
              {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : null}
              导入
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => (!open ? setDeleteTarget(null) : null)}>
        <DialogContent className="rounded-2xl">
          <DialogHeader>
            <DialogTitle>删除模板</DialogTitle>
            <DialogDescription>确认删除“{deleteTarget?.title}”吗？</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" className="rounded-xl" onClick={() => setDeleteTarget(null)} disabled={isSaving}>
              取消
            </Button>
            <Button className="rounded-xl bg-rose-600 text-white hover:bg-rose-700" onClick={() => void handleDelete()} disabled={isSaving}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={bulkDeleteOpen} onOpenChange={setBulkDeleteOpen}>
        <DialogContent className="rounded-2xl">
          <DialogHeader>
            <DialogTitle>批量删除模板</DialogTitle>
            <DialogDescription>确认删除已选择的 {selectedIds.length} 个模板吗？内置模板不会被删除。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" className="rounded-xl" onClick={() => setBulkDeleteOpen(false)} disabled={isSaving}>
              取消
            </Button>
            <Button className="rounded-xl bg-rose-600 text-white hover:bg-rose-700" onClick={() => void handleBulkDelete()} disabled={isSaving || selectedIds.length === 0}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

export default function PromptTemplatesPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin", "user"]);
  if (isCheckingAuth || !session) {
    return <div className="flex min-h-[40vh] items-center justify-center"><LoaderCircle className="size-5 animate-spin text-stone-400" /></div>;
  }
  return <PromptTemplatesContent />;
}
