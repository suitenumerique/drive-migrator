/* eslint-disable react/no-unescaped-entities */
export default function LegalNotice() {
  return (
    <div className="container">
      <h1>Mention légale</h1>
      <div>
        <h2>Éditeur</h2>
        <p>
          Direction interministérielle des affaires numériques (DINUM), 20
          avenue de Segur 75007 Paris.
        </p>
        <h2>Directeur de la publication</h2>
        <p>Stéphanie Schaer: Directrice numérique interministériel (DINUM).</p>
        <h2>Copyright</h2>
        <p>
          Illustration: <span>DINUM</span>
        </p>
        <h2>Plus d'infos ?</h2>
        <p>
          L'équipe responsable de l'espace de travail numérique "La Suite
          numérique" peut être contactée directement à l'adresse{' '}
          <a href="mailto:lasuite@modernisation.gouv.fr">
            lasuite@modernisation.gouv.fr
          </a>
          .
        </p>
      </div>
    </div>
  );
}
