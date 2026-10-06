"use client";
import { useState, type ComponentType } from "react";
import { useTranslations } from "next-intl";
import { tourActions, useTourOpen } from "@/lib/tour";
import { Modal } from "./Modal";
import { AnalyzeIcon, BellIcon, CameraIcon, ChevronIcon, HomeIcon, SettingsIcon, SuggestIcon } from "./icons";

const SLIDES: { id: string; Icon: ComponentType<{ className?: string }> }[] = [
  { id: "welcome", Icon: SuggestIcon },
  { id: "portfolio", Icon: CameraIcon },
  { id: "home", Icon: HomeIcon },
  { id: "analyze", Icon: AnalyzeIcon },
  { id: "alerts", Icon: BellIcon },
  { id: "rules", Icon: SettingsIcon },
];

/** Short feature tour shown once on the first entrance (and from Settings). Skip or finish both mark it seen. */
export function WelcomeTour() {
  const open = useTourOpen();
  if (!open) return null;
  return <TourDialog />;
}

function TourDialog() {
  const t = useTranslations("tour");
  const [i, setI] = useState(0);
  const last = i === SLIDES.length - 1;
  const { id, Icon } = SLIDES[i];
  return (
    <Modal title={t("title")} onClose={tourActions.markSeen}>
      <div className="space-y-3 text-center" data-testid="tour">
        <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-surface-2 text-brand"><Icon className="h-8 w-8" /></span>
        <h3 className="text-xl font-bold">{t(`slides.${id}.title`)}</h3>
        <p className="text-muted">{t(`slides.${id}.body`)}</p>
        <p className="text-sm text-muted" aria-live="polite">{t("step", { n: i + 1, total: SLIDES.length })}</p>
        <div className="flex justify-center gap-2" aria-hidden="true">
          {SLIDES.map((s, k) => <span key={s.id} className={`h-2 w-2 rounded-full ${k === i ? "bg-brand" : "bg-line"}`} />)}
        </div>
        <div className="flex items-center gap-2 pt-1">
          <button type="button" className="min-h-11 rounded-xl px-3 text-sm text-muted hover:bg-surface-2" onClick={tourActions.markSeen}>{t("skip")}</button>
          <span className="flex-1" />
          {i > 0 && (
            <button type="button" className="btn-secondary min-h-11 gap-1" onClick={() => setI(i - 1)}>
              <ChevronIcon className="h-4 w-4 rotate-180 rtl:rotate-0" />{t("back")}
            </button>
          )}
          {last ? (
            <button type="button" className="btn-primary" onClick={tourActions.markSeen}>{t("start")}</button>
          ) : (
            <button type="button" className="btn-primary gap-1" onClick={() => setI(i + 1)}>{t("next")}<ChevronIcon className="h-4 w-4" /></button>
          )}
        </div>
      </div>
    </Modal>
  );
}
