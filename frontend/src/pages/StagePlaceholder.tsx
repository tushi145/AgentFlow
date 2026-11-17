import { Construction } from "lucide-react";

import { EmptyState, PageHeader } from "@/components/common";

/** Honest placeholder for pages scheduled in a later build stage — shows no fake data. */
export function StagePlaceholder({ title, stage, description }: { title: string; stage: string; description: string }) {
  return (
    <>
      <PageHeader title={title} description={description} />
      <EmptyState
        icon={Construction}
        title={`Planned for stage ${stage}`}
        description="This page is part of the approved frontend roadmap and will be connected to the existing backend API in that stage. No data is shown until then."
      />
    </>
  );
}

export function NotFoundPage() {
  return (
    <EmptyState title="Page not found" description="The page you requested does not exist." className="mt-10" />
  );
}
