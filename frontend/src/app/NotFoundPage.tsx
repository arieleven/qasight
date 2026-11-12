import { Link } from "react-router-dom";

import { MuscleShrug } from "../components/mascot";
import { EmptyState, buttonClasses } from "../components/ui";
import { usePageTitle } from "../lib/usePageTitle";

export function NotFoundPage() {
  usePageTitle("Page not found");
  return (
    <EmptyState
      mascot={<MuscleShrug />}
      headingLevel="h1"
      title="Page not found"
      message="There is nothing at this address."
      action={
        <Link to="/" className={buttonClasses("primary", "sm")}>
          Go to overview
        </Link>
      }
    />
  );
}
