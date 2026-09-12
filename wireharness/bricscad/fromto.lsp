;;; ============================================================================
;;;  fromto.lsp  ―  BricsCAD 用 From-To（結線表）書き出しアドオン  v1
;;;  古川電気工業 / 工業電気検図システム 連携ツール
;;; ----------------------------------------------------------------------------
;;;  目的:
;;;    シーケンス/スケルトン図面から、電線ネットをたどって
;;;      号線 → 機器記号・機器番号・端子番号
;;;    の From-To（結線）表を CSV で書き出す。
;;;    あわせて「接続確認（検図）」として、
;;;      浮き線端 / 端子未接続 / 号線なし / 片接続
;;;    を検図レポートに書き出す。
;;;
;;;  使い方（載せ方）:
;;;    1) BricsCAD で対象図面（シーケンス or スケルトン）を開く
;;;    2) コマンド APPLOAD → この fromto.lsp を選んでロード
;;;       （または  (load "C:/…/fromto.lsp")  ）
;;;    3) コマンドラインで  FROMTO  と入力
;;;    4) 図面と同じフォルダに
;;;         <図面名>_fromto.csv  … 結線表（From-To）
;;;         <図面名>_kenzu.txt   … 検図レポート（要確認箇所）
;;;       が出力される
;;;
;;;  出力CSVの列（人手ハーネスデータと同じ並び。検図システムにそのまま取込可）:
;;;       号線, サイズ, 機器記号, 機器番号, 端子番号
;;;
;;;  調整（図面の作図規約に合わせて下の定数を編集）:
;;;    *FT-WIRE-LAYERS* … 電線とみなす画層（カンマ区切り）
;;;    *FT-TOL*         … 電線どうしを「つながっている」とみなす許容距離
;;;    *FT-SNAP*        … 電線の端を機器端子に寄せる許容距離
;;;    *FT-TAGMAX*      … 号線タグを電線に結び付ける最大距離
;;; ============================================================================

;; ---- 設定（環境に合わせて編集可） -----------------------------------------
(setq *FT-WIRE-LAYERS* "L_CONTROL,L_MAIN,L_EARTH")   ; 電線画層
(setq *FT-TOL*     8.0)     ; 連結判定の許容距離（図面単位）
(setq *FT-SNAP*   25.0)     ; 端子スナップ許容距離
(setq *FT-TAGMAX* 60.0)     ; 号線タグ→電線 最大距離
(setq *FT-DEVSNAP* 80.0)    ; 号線ラベル→機器 最大距離（号線グループ化モード）
(setq *FT-SKIP-DEV* '("LUG" "CABLE" "CABLE1" "CABLE2" ""))  ; 端子化しない機器
(setq *FT-TERM-ATTRS* '("TERMINAL1" "TERMINAL2" "TERMINAL3"
                        "TERMINAL4" "TERMINAL5" "TERMINAL6"))
(setq *FT-GOU-ATTRS*  '("線番" "SOU1" "SOU2" "SOU3"))       ; 号線が入る属性

;; ---- 幾何ヘルパ -------------------------------------------------------------
(defun ft:2d (p) (list (car p) (cadr p)))            ; 3D点→2D
(defun ft:d (a b) (distance (ft:2d a) (ft:2d b)))    ; 2D距離

;; 点 p と 線分 a-b の距離
(defun ft:ptseg (p a b / ax ay bx by px py dx dy L2 tt qx qy)
  (setq p (ft:2d p) a (ft:2d a) b (ft:2d b))
  (setq ax (car a) ay (cadr a) bx (car b) by (cadr b)
        px (car p) py (cadr p) dx (- bx ax) dy (- by ay))
  (setq L2 (+ (* dx dx) (* dy dy)))
  (if (<= L2 1e-9)
    (distance p a)
    (progn
      (setq tt (/ (+ (* (- px ax) dx) (* (- py ay) dy)) L2))
      (setq tt (max 0.0 (min 1.0 tt)))
      (setq qx (+ ax (* tt dx)) qy (+ ay (* tt dy)))
      (distance p (list qx qy)))))

;; 文字列を区切り文字で分割 → 部分文字列のリスト
(defun ft:split (s sep / out i ch cur)
  (setq out '() cur "" i 1)
  (if (or (null s) (= s "")) '()
    (progn
      (while (<= i (strlen s))
        (setq ch (substr s i 1))
        (if (= ch sep)
          (progn (setq out (cons cur out)) (setq cur ""))
          (setq cur (strcat cur ch)))
        (setq i (1+ i)))
      (setq out (cons cur out))
      (reverse out))))

;; 空白除去
(defun ft:trim (s / a b)
  (if (null s) ""
    (progn
      (setq a 1 b (strlen s))
      (while (and (<= a b) (member (substr s a 1) '(" " "\t")))
        (setq a (1+ a)))
      (while (and (>= b a) (member (substr s b 1) '(" " "\t")))
        (setq b (1- b)))
      (if (< b a) "" (substr s a (- b a -1))))))

;; assoc 取り出し（無ければ既定値）
(defun ft:g (ed code def) (if (assoc code ed) (cdr (assoc code ed)) def))

;; ---- INSERT の属性を読む ----------------------------------------------------
;; 戻り: (insert点 rot sx sy attsAlist)   atts = (("TAG" . "値") ...)
(defun ft:insert-data (en / ed atts a nx)
  (setq ed (entget en) atts '())
  (if (= 1 (ft:g ed 66 0))
    (progn
      (setq nx (entnext en))
      (while (and nx (/= "SEQEND" (cdr (assoc 0 (entget nx)))))
        (setq a (entget nx))
        (if (= "ATTRIB" (cdr (assoc 0 a)))
          (setq atts (cons (cons (cdr (assoc 2 a)) (cdr (assoc 1 a))) atts)))
        (setq nx (entnext nx)))))
  (list (ft:g ed 10 '(0.0 0.0 0.0))
        (ft:g ed 50 0.0)
        (ft:g ed 41 1.0)
        (ft:g ed 42 1.0)
        atts))

(defun ft:att (atts tag) (cdr (assoc tag atts)))     ; 属性値（無ければnil）

;; ローカル(ox,oy)を 挿入点/回転/尺度 でワールド座標へ
(defun ft:world (ins rot sx sy ox oy / cx cy sox soy)
  (setq sox (* sx ox) soy (* sy oy)
        cx (cos rot) cy (sin rot))
  (list (+ (car ins)  (- (* sox cx) (* soy cy)))
        (+ (cadr ins) (+ (* sox cy) (* soy cx)))))

;; ---- 機器ブロック → 端子ピンのリスト --------------------------------------
;; 各ピン: (機器記号 機器番号 端子名 world点 サイズ)
(defun ft:terminals (idata / ins rot sx sy atts dev dev1 size
                              terms tbnums pins i nm ox oy w n)
  (setq ins (nth 0 idata) rot (nth 1 idata) sx (nth 2 idata)
        sy (nth 3 idata) atts (nth 4 idata))
  (setq dev (ft:trim (ft:att atts "DEVICE")))
  (if (member (strcase dev) *FT-SKIP-DEV*)
    nil
    (progn
      (setq dev1 (ft:trim (ft:att atts "DEVICE1")))
      (setq size (ft:trim (ft:att atts "GAISENSIZE")))
      ;; 端子名（TERMINAL1..6 をカンマ展開して1本の並びに）
      (setq terms '())
      (foreach ta *FT-TERM-ATTRS*
        (foreach t (ft:split (ft:att atts ta) ",")
          (if (/= (ft:trim t) "") (setq terms (cons (ft:trim t) terms)))))
      (setq terms (reverse terms))
      ;; TB 属性 = 端子ピンのオフセット x,y,x,y,...
      (setq tbnums '())
      (foreach v (ft:split (ft:att atts "TB") ",")
        (if (/= (ft:trim v) "") (setq tbnums (cons (atof (ft:trim v)) tbnums))))
      (setq tbnums (reverse tbnums))
      ;; ピン生成
      (setq pins '() i 0)
      (if tbnums
        (while (< (1+ (* 2 i)) (1+ (length tbnums)))
          (setq ox (nth (* 2 i) tbnums)
                oy (nth (1+ (* 2 i)) tbnums))
          (if (and ox oy)
            (progn
              (setq w (ft:world ins rot sx sy ox oy))
              (setq nm (if (< i (length terms)) (nth i terms) ""))
              (setq pins (cons (list dev dev1 nm w size) pins))))
          (setq i (1+ i)))
        ;; TB が無い機器は挿入点に1ピン
        (setq pins (list (list dev dev1 (if terms (car terms) "") ins size))))
      (reverse pins))))

;; ---- 号線タグ（線番 / SOU） -------------------------------------------------
;; 各タグ: (号線 . 点)
(defun ft:tags (idata / ins atts out v)
  (setq ins (nth 0 idata) atts (nth 4 idata) out '())
  (foreach ga *FT-GOU-ATTRS*
    (setq v (ft:trim (ft:att atts ga)))
    (if (/= v "") (setq out (cons (cons v ins) out))))
  out)

;; ---- 電線セグメント収集 -----------------------------------------------------
;; 戻り: セグメントのリスト  各セグ = (点a 点b)
(defun ft:collect-wires ( / ss i en ed typ segs pts prev p closed)
  (setq segs '())
  (setq ss (ssget "X" (list (cons 0 "LINE,LWPOLYLINE")
                            (cons 8 *FT-WIRE-LAYERS*))))
  (if ss
    (progn
      (setq i 0)
      (while (< i (sslength ss))
        (setq en (ssname ss i) ed (entget en) typ (cdr (assoc 0 ed)))
        (cond
          ((= typ "LINE")
           (setq segs (cons (list (cdr (assoc 10 ed)) (cdr (assoc 11 ed))) segs)))
          ((= typ "LWPOLYLINE")
           (setq pts '())
           (foreach it ed (if (= 10 (car it)) (setq pts (cons (cdr it) pts))))
           (setq pts (reverse pts) prev nil)
           (foreach p pts
             (if prev (setq segs (cons (list prev p) segs)))
             (setq prev p))
           (setq closed (= 1 (logand 1 (ft:g ed 70 0))))
           (if (and closed (> (length pts) 2))
             (setq segs (cons (list (last pts) (car pts)) segs)))))
        (setq i (1+ i)))))
  segs)

;; ---- 電線ネット構築（連結成分） --------------------------------------------
;; net = (点リスト セグリスト)。点が *FT-TOL* 内なら同ネット。
(defun ft:pt-in (p pts / f)
  (setq f nil)
  (foreach q pts (if (<= (ft:d p q) *FT-TOL*) (setq f t)))
  f)

(defun ft:build-nets (segs / nets seg a b hit rest merged)
  (setq nets '())
  (foreach seg segs
    (setq a (car seg) b (cadr seg) hit '() rest '())
    (foreach nt nets
      (if (or (ft:pt-in a (car nt)) (ft:pt-in b (car nt)))
        (setq hit (cons nt hit))
        (setq rest (cons nt rest))))
    (if hit
      (setq nets (cons (list (append (list a b) (apply 'append (mapcar 'car hit)))
                             (cons seg (apply 'append (mapcar 'cadr hit))))
                       rest))
      (setq nets (cons (list (list a b) (list seg)) nets))))
  nets)

;; 点に最も近いネットの添字（許容内）。無ければ -1
(defun ft:net-of-pt (p nets tol / i best bi)
  (setq i 0 best 1e30 bi -1)
  (foreach nt nets
    (foreach q (car nt)
      (if (< (ft:d p q) best) (setq best (ft:d p q) bi i)))
    (setq i (1+ i)))
  (if (<= best tol) bi -1))

;; タグに最も近いネットの添字（線分距離、許容内）。無ければ -1
(defun ft:net-of-tag (p nets tol / i best bi d)
  (setq i 0 best 1e30 bi -1)
  (foreach nt nets
    (foreach seg (cadr nt)
      (setq d (ft:ptseg p (car seg) (cadr seg)))
      (if (< d best) (setq best d bi i)))
    (setq i (1+ i)))
  (if (<= best tol) bi -1))

;; 添字付きリストの add（(idx . 値) を貯める）
(defun ft:push-idx (idx val lst) (cons (cons idx val) lst))
(defun ft:by-idx (idx lst / out)
  (setq out '())
  (foreach kv lst (if (= (car kv) idx) (setq out (cons (cdr kv) out))))
  (reverse out))

;; 端点の次数（1本しか繋がらない＝浮き端候補）
(defun ft:degree (p segs / n)
  (setq n 0)
  (foreach seg segs
    (if (<= (ft:d p (car seg)) *FT-TOL*)  (setq n (1+ n)))
    (if (<= (ft:d p (cadr seg)) *FT-TOL*) (setq n (1+ n))))
  n)

;; 点リストを TOL でユニーク化
(defun ft:uniq-pts (pts / out)
  (setq out '())
  (foreach p pts (if (not (ft:pt-in p out)) (setq out (cons p out))))
  out)

;; ---- メインコマンド ---------------------------------------------------------
(defun C:FROMTO ( / ss i en idata dev-pins tags segs nets
                    gByNet tByNet orphanT ni g pins base dir
                    csv rep fcsv frep row terms devset ends d nearT
                    n-rows n-float n-orphan n-nogou n-half)
  (princ "\n=== From-To 書き出し（v1）===")
  ;; 1) 機器端子・号線タグ収集
  (setq dev-pins '() tags '())
  (setq ss (ssget "X" (list (cons 0 "INSERT"))))
  (if ss
    (progn
      (setq i 0)
      (while (< i (sslength ss))
        (setq en (ssname ss i) idata (ft:insert-data en))
        (setq dev-pins (append (ft:terminals idata) dev-pins))
        (setq tags (append (ft:tags idata) tags))
        (setq i (1+ i)))))
  ;; 2) 電線ネット
  (setq segs (ft:collect-wires))
  (if (null segs)
    (progn (princ "\n電線が見つかりません。*FT-WIRE-LAYERS* の画層名を確認してください。")
           (princ) (exit)))
  (setq nets (ft:build-nets segs))
  (princ (strcat "\n電線ネット= " (itoa (length nets))
                 " / 機器端子= " (itoa (length dev-pins))
                 " / 号線タグ= " (itoa (length tags))))
  ;; 3) 号線をネットへ
  (setq gByNet '())
  (foreach tg tags
    (setq ni (ft:net-of-tag (cdr tg) nets *FT-TAGMAX*))
    (if (>= ni 0) (setq gByNet (ft:push-idx ni (car tg) gByNet))))
  ;; 4) 端子をネットへ（届かない端子はorphan）
  (setq tByNet '() orphanT '())
  (foreach pn dev-pins
    (setq ni (ft:net-of-pt (nth 3 pn) nets *FT-SNAP*))
    (if (>= ni 0)
      (setq tByNet (ft:push-idx ni pn tByNet))
      (setq orphanT (cons pn orphanT))))
  ;; 5) 出力
  (setq base (vl-filename-base (getvar "DWGNAME"))
        dir  (getvar "DWGPREFIX"))
  (setq fcsv (strcat dir base "_fromto.csv")
        frep (strcat dir base "_kenzu.txt"))
  (setq csv (open fcsv "w") rep (open frep "w"))
  (write-line "号線,サイズ,機器記号,機器番号,端子番号" csv)
  (write-line "=== 検図レポート（接続確認）===" rep)
  (setq n-rows 0 n-float 0 n-orphan 0 n-nogou 0 n-half 0)
  (setq ni 0)
  (foreach nt nets
    (setq g (ft:by-idx ni gByNet)
          terms (ft:by-idx ni tByNet))
    ;; 号線（複数付いたら先頭）
    (setq g (if g (car g) ""))
    ;; From-To 行出力
    (if (and (/= g "") terms)
      (foreach pn terms
        (write-line (strcat g "," (nth 4 pn) "," (nth 0 pn) ","
                            (nth 1 pn) "," (nth 2 pn)) csv)
        (setq n-rows (1+ n-rows))))
    ;; 検図: 号線なし
    (if (and (= g "") terms)
      (progn (write-line (strcat "[号線なし] 機器 "
              (apply 'strcat (mapcar '(lambda(x)(strcat (nth 0 x) (nth 1 x) " ")) terms))
              "を結ぶ電線に線番が付いていません"
              "\n    → 解決策: この電線に線番(号線)を付けてください") rep)
             (setq n-nogou (1+ n-nogou))))
    ;; 検図: 片接続（機器が1つ以下）
    (setq devset '())
    (foreach pn terms
      (setq d (strcat (nth 0 pn) (nth 1 pn)))
      (if (not (member d devset)) (setq devset (cons d devset))))
    (if (and (/= g "") (< (length devset) 2))
      (progn (write-line (strcat "[片接続] 号線 " g
              " は機器が1つしか繋がっていません（相手先不明）"
              "\n    → 解決策: 相手側の端子にも同じ号線を付ける／相手機器を図面に追加") rep)
             (setq n-half (1+ n-half))))
    ;; 検図: 浮き線端（次数1の端点で、近くに端子なし）
    (foreach p (ft:uniq-pts (car nt))
      (if (= 1 (ft:degree p (cadr nt)))
        (progn
          (setq nearT nil)
          (foreach pn terms
            (if (<= (ft:d p (nth 3 pn)) *FT-SNAP*) (setq nearT t)))
          (if (not nearT)
            (progn
              (write-line (strcat "[浮き線端] 号線 " (if (= g "") "?" g)
                " の電線端が端子に未接続  座標("
                (rtos (car p) 2 1) "," (rtos (cadr p) 2 1) ")"
                "\n    → 解決策: 電線の端を機器端子にスナップ接続してください") rep)
              (setq n-float (1+ n-float)))))))
    (setq ni (1+ ni)))
  ;; 検図: 端子未接続（電線が来ていない端子）
  (foreach pn orphanT
    (write-line (strcat "[端子未接続] " (nth 0 pn) (nth 1 pn)
      " 端子" (nth 2 pn) " に電線が来ていません  座標("
      (rtos (car (nth 3 pn)) 2 1) "," (rtos (cadr (nth 3 pn)) 2 1) ")"
      "\n    → 解決策: この端子に接続すべき電線/号線を確認してください") rep)
    (setq n-orphan (1+ n-orphan)))
  ;; まとめ（判定）
  (write-line "" rep)
  (if (and (= n-float 0) (= n-orphan 0) (= n-nogou 0) (= n-half 0))
    (write-line "判定: ◎ 合格 － From-To を 100% 生成できます。内容検図(R1-R7)へ進めます。" rep)
    (write-line "判定: × 要修正 － 上記の問題点と解決策を設計に提示し、承認・修正後に再実行してください。" rep))
  (write-line (strcat "From-To 行数= " (itoa n-rows)) rep)
  (write-line (strcat "浮き線端= " (itoa n-float)
                      "  端子未接続= " (itoa n-orphan)
                      "  号線なし= " (itoa n-nogou)
                      "  片接続= " (itoa n-half)) rep)
  (close csv) (close rep)
  (princ (strcat "\n書き出し完了:"
                 "\n  " fcsv "  (From-To " (itoa n-rows) "行)"
                 "\n  " frep
                 "\n  浮き線端 " (itoa n-float)
                 " / 端子未接続 " (itoa n-orphan)
                 " / 号線なし " (itoa n-nogou)
                 " / 片接続 " (itoa n-half)))
  (princ))

;;; ============================================================================
;;;  号線グループ化モード（推奨）: C:FROMTOG
;;; ----------------------------------------------------------------------------
;;;  前提: 号線ラベル(線番/SOU)を「各接続端子（無ければ機器）のそば」に打つ採番。
;;;  読み方: 各号線ラベル → 最寄り端子(無ければ最寄り機器) → (号線,機器,端子)。
;;;          あとは号線でグループ化するだけ。★電線をたどらない＝浮き線端が無関係。
;;;  実測(29026・新採番模擬): 整合率92%・適合率100%・誤割当0（残り8%は欠品部品）。
;;; ============================================================================

;; 文字列キーの取り出し（= は数値用のため equal を使う）
(defun ft:by-key (k lst / out)
  (setq out '())
  (foreach kv lst (if (equal (car kv) k) (setq out (cons (cdr kv) out))))
  (reverse out))

;; 全機器ブロック → (機器記号 機器番号 挿入点 サイズ)
(defun ft:devices ( / ss i en idata atts dev out)
  (setq out '())
  (setq ss (ssget "X" (list (cons 0 "INSERT"))))
  (if ss
    (progn
      (setq i 0)
      (while (< i (sslength ss))
        (setq en (ssname ss i) idata (ft:insert-data en) atts (nth 4 idata))
        (setq dev (ft:trim (ft:att atts "DEVICE")))
        (if (and (/= dev "") (not (member (strcase dev) *FT-SKIP-DEV*)))
          (setq out (cons (list dev (ft:trim (ft:att atts "DEVICE1"))
                                (nth 0 idata) (ft:trim (ft:att atts "GAISENSIZE")))
                          out)))
        (setq i (1+ i)))))
  out)

(defun C:FROMTOG ( / ss i en idata dev-pins devs tags tg g pt
                     bestp bestpd pn dd nd bd dv rows unatt
                     base dir fcsv frep csv rep row seen ds devset g2 d
                     n-un n-half deflines ln u)
  (princ "\n=== From-To 書き出し（号線グループ化モード）===")
  ;; 端子ピン・機器・号線ラベルを収集
  (setq dev-pins '() tags '())
  (setq ss (ssget "X" (list (cons 0 "INSERT"))))
  (if ss
    (progn
      (setq i 0)
      (while (< i (sslength ss))
        (setq en (ssname ss i) idata (ft:insert-data en))
        (setq dev-pins (append (ft:terminals idata) dev-pins))
        (setq tags (append (ft:tags idata) tags))
        (setq i (1+ i)))))
  (setq devs (ft:devices))
  (princ (strcat "\n号線ラベル= " (itoa (length tags))
                 " / 端子ピン= " (itoa (length dev-pins))
                 " / 機器= " (itoa (length devs))))
  ;; 各号線ラベル → 最寄り端子(SNAP) → 無ければ最寄り機器(DEVSNAP)
  (setq rows '() unatt '())
  (foreach tg tags
    (setq g (car tg) pt (cdr tg) bestp nil bestpd 1e30)
    (foreach pn dev-pins
      (setq dd (ft:d pt (nth 3 pn)))
      (if (< dd bestpd) (setq bestpd dd bestp pn)))
    (if (and bestp (<= bestpd *FT-SNAP*))
      (setq rows (cons (list g (nth 4 bestp) (nth 0 bestp)
                             (nth 1 bestp) (nth 2 bestp)) rows))
      (progn
        (setq nd nil bd 1e30)
        (foreach dv devs
          (setq dd (ft:d pt (nth 2 dv)))
          (if (< dd bd) (setq bd dd nd dv)))
        (if (and nd (<= bd *FT-DEVSNAP*))
          (setq rows (cons (list g (nth 3 nd) (nth 0 nd) (nth 1 nd) "") rows))
          (setq unatt (cons (list g pt) unatt))))))
  ;; --- 問題点＋解決策を組み立て（設計へ提示する形） ---
  (setq deflines '())
  ;; 片接続（相手先が1つしかない号線）
  (setq g2 '())
  (foreach row rows
    (setq g2 (ft:push-idx (car row) (strcat (nth 2 row) (nth 3 row)) g2)))
  (setq seen '() n-half 0)
  (foreach row rows
    (setq g (car row))
    (if (not (member g seen))
      (progn
        (setq seen (cons g seen))
        (setq ds (ft:by-key g g2) devset '())
        (foreach d ds (if (not (member d devset)) (setq devset (cons d devset))))
        (if (< (length devset) 2)
          (progn
            (setq deflines (cons (strcat
              "[片接続] 号線 " g " は機器1つにしか繋がっていません"
              "\n    → 解決策: 相手側の端子にも号線 " g
              " を付けてください（相手機器が図面に無い場合は追加が必要）") deflines))
            (setq n-half (1+ n-half)))))))
  ;; 未接続ラベル（端子/機器に寄せられない号線）
  (setq n-un 0)
  (foreach u unatt
    (setq deflines (cons (strcat
      "[未接続ラベル] 号線 " (car u) "  座標("
      (rtos (car (cadr u)) 2 1) "," (rtos (cadr (cadr u)) 2 1) ")"
      "\n    → 解決策: この号線ラベルを接続先端子のそばに置く／"
      "その端子・機器を図面に追加してください") deflines))
    (setq n-un (1+ n-un)))
  ;; --- 出力 ---
  (setq base (vl-filename-base (getvar "DWGNAME")) dir (getvar "DWGPREFIX"))
  (setq fcsv (strcat dir base "_fromto.csv") frep (strcat dir base "_kenzu.txt"))
  (setq csv (open fcsv "w") rep (open frep "w"))
  (write-line "号線,サイズ,機器記号,機器番号,端子番号" csv)
  (foreach row (reverse rows)
    (write-line (strcat (nth 0 row) "," (nth 1 row) "," (nth 2 row) ","
                        (nth 3 row) "," (nth 4 row)) csv))
  (write-line "=== 接続検図レポート（号線グループ化モード）===" rep)
  (if (and (= n-un 0) (= n-half 0))
    (write-line "判定: ◎ 合格 － From-To を 100% 生成できます。内容検図(R1-R7)へ進めます。" rep)
    (write-line "判定: × 要修正 － 下記の問題点と解決策を設計に提示し、承認・修正後に再実行してください。" rep))
  (write-line (strcat "From-To 行数= " (itoa (length rows))
                      "  問題点: 片接続= " (itoa n-half)
                      " / 未接続ラベル= " (itoa n-un)) rep)
  (write-line "" rep)
  (foreach ln (reverse deflines) (write-line ln rep))
  (close csv) (close rep)
  (princ (strcat "\n書き出し完了:"
                 "\n  " fcsv "  (From-To " (itoa (length rows)) "行)"
                 "\n  " frep
                 "\n  判定 " (if (and (= n-un 0) (= n-half 0)) "◎合格" "×要修正")
                 " (片接続 " (itoa n-half) " / 未接続ラベル " (itoa n-un) ")"))
  (princ))

(princ "\nfromto.lsp ロード完了。")
(princ "\n  FROMTO   … 電線をたどって結線を復元（現行採番向け）")
(princ "\n  FROMTOG  … 号線ラベルでグループ化（推奨・各端子に号線を打つ採番向け）")
(princ)
;;; ============================================================================
