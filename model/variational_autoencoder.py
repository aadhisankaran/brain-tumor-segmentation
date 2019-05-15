import tensorflow as tf

from model.group_norm import GroupNormalization
from model.layers import ConvBlock
from utils.constants import *


def sample(latent_args):
    """Samples from the Gaussian given by mean and variance."""
    z_mean, z_logvar = latent_args
    eps = tf.random.normal(shape=z_mean.shape, dtype=tf.float32)
    return z_mean + tf.math.exp(0.5 * z_logvar) * eps


class VariationalAutoencoderBlock(tf.keras.layers.Layer):
    def __init__(self,
                 filters,
                 kernel_size=3,
                 data_format='channels_last',
                 groups=8,
                 kernel_regularizer=None):
        """Initializes a variational autoencoder block.

            See https://arxiv.org/pdf/1810.11654.pdf for more details.
            Similar to a decoder block, a variational autoencoder block
            consists of pointwise convolution, upsampling, and a conv
            block. There is no residual passed through from the encoder
            in this branch of the network.

            Args:
                filters: int
                    The number of filters to use in the 3D convolutional
                    block. The output layer of this green block will have
                    this many number of channels.
                kernel_size: kernel_size: (int, int, int), optional
                    The size of all convolutional kernels. Defaults to 3,
                    as used in the paper.
                data_format: str, optional
                    The format of the input data. Must be either 'channels_last'
                    or 'channels_first'. Defaults to `channels_last` for CPU
                    development. 'channels_first is used in the paper.
                groups: int, optional
                    The size of each group for GroupNormalization. Defaults to
                    8, as used in the paper.
                kernel_regularizer: regularizer callable for convolutional
                    kernels.
        """
        super(VariationalAutoencoderBlock, self).__init__()

        self.conv3d_ptwise = tf.keras.layers.Conv3D(
                                filters=filters,
                                kernel_size=1,
                                strides=1,
                                padding='same',
                                data_format=data_format,
                                kernel_regularizer=kernel_regularizer)
        self.upsample = tf.keras.layers.UpSampling3D(
                                size=2,
                                data_format=data_format)
        self.conv_block = ConvBlock(
                                filters=filters,
                                kernel_size=kernel_size,
                                data_format=data_format,
                                groups=groups,
                                kernel_regularizer=kernel_regularizer)

    def call(self, x):
        """Returns the forward pass of one VariationalAutoencoderBlock.

            { Conv3D_ptwise -> Upsample3D -> ConvBlock }
        """
        x = self.conv3d_ptwise(x)
        x = self.upsample(x)
        x = self.conv_block(x)
        return x


class VariationalAutoencoder(tf.keras.layers.Layer):
    def __init__(self,
                 data_format='channels_last',
                 groups=8,
                 kernel_size=3,
                 kernel_regularizer=None):
        """Initializes the Variational Autoencoder branch.
        
            See https://arxiv.org/pdf/1810.11654.pdf for more details.
            The variational autoencoder reconstructs the original image
            given to the encoder to regularize the encoder. To do so,
            it samples from a learned Gaussian and upsamples.

            Args:
                data_format: str, optional
                    The format of the input data. Must be either 'channels_last'
                    or 'channels_first'. Defaults to `channels_last` for CPU
                    development. 'channels_first is used in the paper.
                groups: int, optional
                    The size of each group for GroupNormalization. Defaults to
                    8, as used in the paper.
                kernel_size: kernel_size: (int, int, int), optional
                    The size of all convolutional kernels. Defaults to 3,
                    as used in the paper.
                kernel_regularizer: regularizer callable for convolutional
                    kernels.
        """
        super(VariationalAutoencoder, self).__init__(name='variational_autoencoder')

        # VD Block
        self.groupnorm_VD = GroupNormalization(
                                groups=groups,
                                axis=-1 if data_format == 'channels_last' else 1)
        self.relu_VD = tf.keras.layers.Activation('relu')
        self.conv3d_VD = tf.keras.layers.Conv3D(
                                filters=VAE_VD_CONV_SIZE,
                                kernel_size=kernel_size,
                                strides=2,
                                padding='same',
                                data_format=data_format,
                                kernel_regularizer=kernel_regularizer)
        self.flatten_VD = tf.keras.layers.Flatten(data_format)
        self.proj_VD = tf.keras.layers.Dense(VAE_VD_BLOCK_SIZE)

        # VDraw Block
        self.sample = tf.keras.layers.Lambda(sample)

        # VU Block
        h = int(H/16)
        w = int(W/16)
        d = int(D/16)
        self.proj_VU = tf.keras.layers.Dense(h * w * d * 1)
        self.relu_VU = tf.keras.layers.Activation('relu')
        self.reshape_VU = tf.keras.layers.Reshape((h, w, d, 1))
        self.conv1d_VU = tf.keras.layers.Conv3D(
                                filters=VAE_VU_BLOCK_SIZE,
                                kernel_size=1,
                                strides=1,
                                padding='same',
                                data_format=data_format,
                                kernel_regularizer=kernel_regularizer)
        self.conv3d_upsample_VU = tf.keras.layers.UpSampling3D(
                                size=2,
                                data_format=data_format)

        self.conv_block_2 = VariationalAutoencoderBlock(
                                filters=VAE_CONV_BLOCK2_SIZE,
                                data_format=data_format,
                                groups=groups,
                                kernel_size=kernel_size,
                                kernel_regularizer=kernel_regularizer)

        self.conv_block_1 = VariationalAutoencoderBlock(
                                filters=VAE_CONV_BLOCK1_SIZE,
                                data_format=data_format,
                                groups=groups,
                                kernel_size=kernel_size,
                                kernel_regularizer=kernel_regularizer)

        self.conv_block_0 = VariationalAutoencoderBlock(
                                filters=VAE_CONV_BLOCK0_SIZE,
                                data_format=data_format,
                                groups=groups,
                                kernel_size=kernel_size,
                                kernel_regularizer=kernel_regularizer)

        self.conv_out = tf.keras.layers.Conv3D(
                                filters=IN_CH,
                                kernel_size=kernel_size,
                                strides=1,
                                padding='same',
                                data_format=data_format,
                                kernel_regularizer=kernel_regularizer)

    def call(self, x):
        """Returns the forward pass of the VariationalAutoencoder.

            {
                VD:        Reduce dimensionality and flatten.
                VDraw:     Sample mean and variance.
                VU:        Reconstruct volumetric input.
                VUp+Block: Upsample and convolve.
                Vend:      Final output convolution.
            }
        """
        # VD Block
        x = self.groupnorm_VD(x)
        x = self.relu_VD(x)
        x = self.conv3d_VD(x)
        x = self.flatten_VD(x)
        x = self.proj_VD(x)

        # VDraw Block
        z_mean = x[:, :VAE_LATENT_SIZE]
        z_logvar = x[:, VAE_LATENT_SIZE:]
        x = self.sample([z_mean, z_logvar])

        # VU Block
        x = self.proj_VU(x)
        x = self.relu_VU(x)
        x = self.reshape_VU(x)
        x = self.conv1d_VU(x)
        x = self.conv3d_upsample_VU(x)

        # VUp2 and VBlock2
        x = self.conv_block_2(x)

        # VUp1 and VBlock1
        x = self.conv_block_1(x)

        # VUp0 and VBlock0
        x = self.conv_block_0(x)

        # Vend
        x = self.conv_out(x)

        return x, z_mean, z_logvar
