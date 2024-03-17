<script lang="ts">
  let prompt = "baba is you";
  let resultURLHash: string | null = null;

  async function handleRender() {
    const response = await fetch("/api/render/tiles", {
      method: "POST",
      headers: {
        "content-type": "application/json"
      },
      body: JSON.stringify({
        prompt
      })
    }).then((req) => req.json());

    // TODO(netux): handle response.error

    resultURLHash = response.resultURLHash;
  }
</script>

<main>
  <h1>WEBAPP IS YOU</h1>

  <p>mock Svelte frontend</p>

  <div class="card">
    <input bind:value={prompt} />
    <button on:click={handleRender}>Render</button>
  </div>

  {#if !!resultURLHash}
    <!-- svelte-ignore a11y-missing-attribute -->
    <img src="/results/{resultURLHash}.gif" />
  {/if}
</main>
