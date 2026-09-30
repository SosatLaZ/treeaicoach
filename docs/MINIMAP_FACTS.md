# Faits mesurés sur de vraies captures (client 2022–2026)

Source : 15 images réelles (wiki LoL 2022 sans perte, 11 images YouTube 2024–2026 en 1280×720, 1 GIF patch 14.20),
5 recadrages annotés dans `scratchpad/real_refs/` (34 icônes). Ces valeurs **priment** sur les hypothèses d'ARCHITECTURE.md.

## Icônes de champions
* **Le joueur local n'a PAS d'anneau jaune** : son anneau est **identique à celui des alliés** (bleu clair).
  → Le détecteur ne peut pas distinguer « self » d'« ally » visuellement. En entraînement, les icônes « self » sont
  étiquetées `ally`. La relation `self` est déterminée par **l'identité** (champion + skin du joueur actif, via l'API Live Client),
  avec en secours l'icône alliée la plus proche du centre du rectangle caméra.
* Pas de priorité d'affichage pour « self » (un ennemi peut être dessiné par-dessus).
* Couleurs d'anneau (sans perte) : **allié / self ≈ RGB(75–81, 140–162, 200–230)** (bleu clair), variante 2024 plus pâle
  bleu-lavande ≈ RGB(133–183, 143–187, 162–204) ; **ennemi ≈ RGB(195–204, 51, 51)**.
  En JPEG (désaturé) : allié ≈ (91–130, 119–155, 134–171), ennemi ≈ (105–190, 34–126, 28–119).
* **Anneau fin** : ≈ 1,5–2 px pour une icône de 27 px → **5–8 % du diamètre**, avec un fin liseré sombre
  ≈ RGB(30–65, 37–75, 55–130) entre l'anneau et le portrait.
* **Diamètre d'icône / largeur minimap : 0,088–0,10** (un peu plus grand quand la minimap est petite).
  → rayon normalisé `r` ≈ 0,044–0,050 (entraînement : diamètre 0,07–0,12 pour la robustesse).
* Rappel (recall) : halo cyan lumineux ≈ RGB(106–113, 180, 201–214), 2–4 px, à 1,1–1,25 × le rayon de l'icône.

## Minimap
* Carré en bas à droite, écart au bord de l'écran ≈ 8–15 px à 1080p (5–10 px à 720p).
* Taille du carré : **0,235–0,239 × hauteur d'écran** (≈ 255 px à 1080p) le plus souvent ; vu aussi 0,294 et 0,311 × H.
* Cadre : fin liseré bronze/or + bande bleu-vert sombre (~9–10 px à 720p) ; encoche décorative en haut à gauche avec un
  bouton losange « ! » bleu-vert qui chevauche le coin de la carte ; icône boutique jaune-vert au coin de la fontaine alliée ;
  portraits des alliés au-dessus ; boutons muet/paramètres en bas à gauche hors du cadre.
* **Toute la texture 512 px (marges noires comprises) est mise à l'échelle sur le carré**, sans recadrage (NCC 0,62–0,65).
* Carte **jamais retournée** (côté rouge : ma base est en haut à droite).
* Zones visibles = texture telle quelle ; murs ≈ RGB(1–3, 2–9, 2–10) (quasi noir).
* **Brouillard de guerre = multiplication uniforme ≈ 0,36 (0,33–0,37) de tous les canaux**, sans teinte, bords doux.

## Autres éléments
* Rectangle caméra : blanc (235–255), 1 px à 720p / ~2 px (≈ 0,007 × largeur) ; taille **0,272–0,279 × 0,151–0,158** de la largeur.
* Sbires : points pleins bleu clair / rouges à contour sombre, **≈ 0,018 × largeur**, en chaînes le long des voies.
* Tourelles : glyphes ≈ 0,04–0,045 × largeur ; tours extérieures avec un badge chiffré (plaques restantes).
* Balises : glyphes ≈ 0,036 × largeur (bleues alliées) ; petits points rouges ≈ 0,02 × largeur.
* Camps : losanges orange ≈ 0,022 × largeur, sabliers jaunes ; camps épiques avec minuteur texte blanc (« 1:17 ») depuis 25.17.
* Pings : anneau rouge pulsé ≈ 0,10 × largeur. Couleurs : générique/en route ≈ RGB(33,188,253), vision ennemie ≈ (248,34,67),
  prudence/disparu ≈ (245,190,15), assistance ≈ (9,215,128).
* Chemin de déplacement : ligne blanche entre mon icône et le point cliqué.
* Mode daltonien (pings) : rouge → magenta RGB(255,79,203), cyan → bleu RGB(24,117,255) ; anneaux non vérifiés
  (→ entraînement avec des teintes d'anneau aléatoires, et identification par portrait).

## Non vérifié
Échelle de minimap par défaut ; couleurs exactes non compressées 2024–26 ; mode daltonien sur les anneaux ; marqueur de TP.
