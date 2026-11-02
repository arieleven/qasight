import { useRef, type HTMLAttributes, type ReactNode } from "react";

import { CopyMenu, cardAnchor } from "./CopyMenu";

export interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title?: ReactNode;
  actions?: ReactNode;
  /** Show the Copy menu (text, table, CSV, chart image, link) next to the title. Default on. */
  copy?: boolean;
}

export function Card({ title, actions, className, children, copy = true, id, ...props }: CardProps) {
  const ref = useRef<HTMLDivElement>(null);
  const titleText = typeof title === "string" ? title : undefined;
  const anchor = id ?? (titleText ? cardAnchor(titleText) : undefined);
  return (
    <div ref={ref} id={anchor} className={`glass-card p-5 ${className ?? ""}`} {...props}>
      {(title || actions) && (
        <div className="mb-4 flex items-center justify-between gap-2">
          {title ? <h2 className="text-base font-bold text-white">{title}</h2> : <span />}
          <div className="flex items-center gap-2">
            {actions}
            {title && copy ? <CopyMenu target={ref} label={titleText} anchor={anchor} /> : null}
          </div>
        </div>
      )}
      {children}
    </div>
  );
}
