import { EmptyState } from "@/components/common";

export function NotFoundPage() {
  return (
    <EmptyState title="Page not found" description="The page you requested does not exist." className="mt-10" />
  );
}
