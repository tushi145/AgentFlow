import { Loader2 } from "lucide-react";
import { type ReactNode, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export function ConfirmDialog({
  open, onOpenChange, title, description, confirmLabel, destructive, pending, onConfirm, withComment,
}: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description: ReactNode; confirmLabel: string;
  destructive?: boolean; pending?: boolean; onConfirm: (comment?: string) => void; withComment?: boolean;
}) {
  const [comment, setComment] = useState("");
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) setComment(""); onOpenChange(o); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {withComment && (
          <div className="space-y-1.5">
            <Label htmlFor="confirm-comment">Comment (optional)</Label>
            <Textarea id="confirm-comment" rows={3} maxLength={1000} value={comment}
                      onChange={(e) => setComment(e.target.value)} />
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>Keep</Button>
          <Button variant={destructive ? "destructive" : "default"} disabled={pending}
                  onClick={() => onConfirm(comment.trim() || undefined)}>
            {pending && <Loader2 className="size-4 animate-spin" />} {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
